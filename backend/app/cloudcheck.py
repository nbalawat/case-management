"""Prove the configured cloud pieces work for real: object storage and the per-client key service.

    python -m app.cloudcheck                    every check, against what the settings point at
    python -m app.cloudcheck storage s3://my-bucket/check/
    python -m app.cloudcheck keys

`storage` writes, lists, reads, overwrites and deletes probe objects under `<url>/_cloudcheck/<run>/` (default
EVIDENCE_URL) and removes them again. `keys` makes a throwaway client key in the configured KEY_PROVIDER, seals a
document under it, rotates it, re-wraps the document, then destroys the key and confirms the document can no longer
be opened. Nothing a real client owns is touched: the probe client is `cfcheck-<run>`.

Exit code 0 when every check passed, 1 otherwise. Each step prints one line, so the output can be pasted as evidence.
On AWS KMS the probe key stays in "pending deletion" for KMS_DELETION_DAYS (AWS's waiting period) and is then deleted
by AWS; it can no longer be used from the moment the check ends.
"""
from __future__ import annotations

import argparse
import secrets
import sys
import time
from collections.abc import Callable

from . import keys
from .config import settings
from .storage import StorageError, open_store, provider_of


class Check:
    def __init__(self, out=print):
        self.out, self.failed = out, 0

    def step(self, name: str, fn: Callable[[], str | None]) -> bool:
        t = time.perf_counter()
        try:
            detail = fn() or ""
        except Exception as e:  # report and carry on: one line per step is the point
            self.failed += 1
            self.out(f"FAIL  {name}: {type(e).__name__}: {str(e)[:300]}")
            return False
        self.out(f"ok    {name} ({(time.perf_counter() - t) * 1000:.0f} ms){': ' + detail if detail else ''}")
        return True


def check_storage(c: Check, url: str, run: str, options: dict | None = None) -> None:
    root = f"{url.rstrip('/')}/_cloudcheck/{run}/"
    try:
        c.out(f"--    storage: {provider_of(url)} at {root}")
    except StorageError as e:
        c.failed += 1
        c.out(f"FAIL  storage location: {e}")
        return
    holder: dict = {}

    def opened():
        holder["s"] = open_store(root, options)
        return None

    if not c.step("open the location", opened):
        return
    s = holder["s"]
    body = b"id,amount\n1,10.00\n2,20.00\n"
    c.step("write an object", lambda: f"fingerprint {s.write('in/part-0.csv', body).fingerprint}")
    c.step("write a second object", lambda: str(s.write("in/deep/part-1.csv", b"x").size) + " bytes")
    c.step("list every object, at any depth", lambda: _expect([o.key for o in s.list()], ["in/deep/part-1.csv", "in/part-0.csv"]))
    c.step("read it back byte for byte", lambda: _expect(_read(s, "in/part-0.csv"), body, show=False))
    before = {o.key: o.fingerprint for o in s.list()}
    c.step("an overwrite changes the fingerprint", lambda: _expect(s.write("in/part-0.csv", body + b"3,30.00\n").fingerprint != before.get("in/part-0.csv"), True))
    c.step("a key cannot climb out of the location", lambda: _refused(lambda: s.write("../escape.csv", b"x")))
    for key in ("in/part-0.csv", "in/deep/part-1.csv"):
        c.step(f"delete {key}", lambda key=key: s.delete(key))
    c.step("nothing is left behind", lambda: _expect(list(s.list()), []))


def check_keys(c: Check, run: str) -> None:
    p = keys.provider()
    if p is None:
        c.out("--    keys: KEY_PROVIDER=none, nothing is sealed: skipped")
        return
    tenant = f"cfcheck-{run}"
    c.out(f"--    keys: {p.name}, probe client {tenant}")
    doc = b"Passport GB 000000000, Probe Person"
    state: dict = {}
    if not c.step("make the client's key", lambda: p.create(tenant).describe() + f" ({p.info(tenant).reference})"):
        return
    try:
        c.step("seal a document (fresh data key, wrapped by the key service)", lambda: _seal(p, tenant, doc, state))
        c.step("open it again", lambda: _expect(keys.open_sealed(p, tenant, "probe/doc.txt", state["blob"]), doc, show=False))
        c.step("another client's name does not open it", lambda: _refused(lambda: keys.open_sealed(p, "cfcheck-other", "probe/doc.txt", state["blob"]), keys.KeyServiceError))
        c.step("rotate the key", lambda: p.rotate(tenant).describe())
        c.step("the document sealed before rotation still opens", lambda: _expect(keys.open_sealed(p, tenant, "probe/doc.txt", state["blob"]), doc, show=False))
        c.step("re-wrap it under the new version", lambda: _rewrap(p, tenant, doc, state))
    finally:
        c.step("destroy the key (crypto-shredding)", lambda: p.destroy(tenant).describe())
    c.step("nothing sealed under it opens any more", lambda: _refused(lambda: keys.open_sealed(p, tenant, "probe/doc.txt", state.get("blob", b"CFK1")), keys.KeyServiceError))


def _seal(p, tenant: str, doc: bytes, state: dict) -> str:
    state["blob"] = keys.seal(p, tenant, "probe/doc.txt", doc)
    assert doc not in state["blob"], "the sealed bytes hold the plaintext"
    return f"{len(state['blob'])} bytes, header {keys.envelope.header(state['blob'])['k'][:12]}..."


def _rewrap(p, tenant: str, doc: bytes, state: dict) -> str:
    again = keys.rewrap(p, tenant, state["blob"])
    _expect(keys.open_sealed(p, tenant, "probe/doc.txt", again), doc, show=False)
    state["blob"] = again
    return keys.envelope.header(again)["k"][:12] + "..."


def _read(s, key: str) -> bytes:
    with s.open(key) as f:
        return f.read()


def _expect(got, want, show: bool = True) -> str:
    if got != want:
        raise AssertionError(f"expected {want!r}, got {got!r}" if show else "the bytes differ")
    return str(got) if show and not isinstance(got, bool) else ""


def _refused(fn, kind: type[Exception] = StorageError) -> str:
    try:
        fn()
    except kind as e:
        return f"refused: {str(e)[:120]}"
    raise AssertionError("it was allowed")


def main(argv: list[str] | None = None, out=print) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.cloudcheck", description=__doc__.split("\n\n")[0])
    ap.add_argument("what", nargs="?", default="all", choices=("all", "storage", "keys"))
    ap.add_argument("url", nargs="?", default="", help="storage location to probe (default EVIDENCE_URL)")
    ap.add_argument("--endpoint-url", default="", help="an emulator's address (MinIO, moto); never needed for a real cloud")
    a = ap.parse_args(argv)
    run = secrets.token_hex(3)
    c = Check(out)
    if a.what in ("all", "storage"):
        check_storage(c, a.url or settings.evidence_url, run, {"endpoint_url": a.endpoint_url} if a.endpoint_url else None)
    if a.what in ("all", "keys"):
        check_keys(c, run)
    out(f"{'PASSED' if not c.failed else f'FAILED ({c.failed} step(s))'}")
    return 1 if c.failed else 0


if __name__ == "__main__":
    sys.exit(main())
