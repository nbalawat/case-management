"""Data pipelines over HTTP, against the seeded demo: who may run them, what each client sees, and where a record came from."""
import pytest

from app.config import settings
from test_client_api import CLIENTS, EVERYONE, OP, PEOPLE, _permissions, h

SOURCE = {"name": "Probe", "dataset": "customers", "root": "landing", "prefix": "core-banking/customers/", "pattern": "**"}


def _loader(tenant):
    return next(p for t, p in EVERYONE if t == tenant and "load_data" in _permissions(t, p))


def _overview(client, tenant, who=None):
    r = client.get("/api/data", headers=h(tenant, who))
    assert r.status_code == 200, r.text
    return r.json()


def test_every_client_starts_with_its_own_sources_and_files_waiting(client):
    seen = set()
    for t in CLIENTS:
        o = _overview(client, t)
        assert [x["dataset"] for x in o["sources"]] == ["customers", "identifiers", "accounts", "transactions"]  # dependency order
        assert all(x["reachable"] and x["waiting"] > 0 and x["waiting"] == x["objects"] for x in o["sources"])
        assert o["runs"] == [] and [loc["id"] for loc in o["locations"]] == ["landing"]
        assert all(str(settings.lake_dir / t) in x["location"] for x in o["sources"])
        ids = {x["id"] for x in o["sources"]}
        assert not ids & seen
        seen |= ids


@pytest.mark.parametrize("tenant,who", EVERYONE, ids=[f"{t}:{p}" for t, p in EVERYONE])
def test_only_people_who_may_load_data_can_change_or_run_anything(client, tenant, who):
    allowed = "load_data" in _permissions(tenant, who)
    sid = _overview(client, tenant, who)["sources"][0]["id"]  # everyone at the client may look
    probes = [("post", "/api/data/preview", SOURCE), ("post", "/api/data/sources", SOURCE | {"dataset": "nonsense"}),
              ("put", f"/api/data/sources/{sid}", SOURCE | {"dataset": "nonsense"}), ("post", "/api/data/sources/no-such-source/run", None),
              ("post", "/api/data/run", {"due_only": True})]
    if allowed:
        probes.pop()  # they may run it, and the batch test below does
    for method, url, body in probes:
        r = getattr(client, method)(url, headers=h(tenant, who), **({"json": body} if body is not None else {}))
        if allowed:
            assert r.status_code != 403, f"{who} was refused {url}"
        else:
            assert r.status_code == 403, f"{who} reached {url}: {r.status_code}"
    assert _overview(client, tenant)["runs"] == []  # a refused request leaves no run behind


def test_identity_is_checked_first(client):
    assert client.get("/api/data", headers=h("nowhere")).status_code == 404
    assert client.post("/api/data/run", headers=h("meridian", "nobody"), json={}).status_code == 403
    assert client.post("/api/data/run", headers=OP | {"X-Tenant": "platform"}, json={}).status_code == 404  # an operator is not a client


def test_a_preview_reads_the_files_and_proposes_a_mapping(client):
    who = _loader("meridian")
    out = client.post("/api/data/preview", headers=h("meridian", who), json=SOURCE)
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["columns"] and body["matching"] == len(body["objects"]) == 1 and body["problem"] is None and body["preview"]
    assert {p["target"] for p in body["proposals"] if p["source"]} >= {"party_id", "name"}
    for bad in ({"root": "elsewhere"}, {"prefix": "../kestrel/"}, {"dataset": "nonsense"}):
        r = client.post("/api/data/preview", headers=h("meridian", who), json=SOURCE | bad)
        assert r.status_code == 422, f"{bad}: {r.status_code} {r.text}"


def txn_run_cases(runs):
    return sum(r["stats"]["cases"] for r in runs)


def test_running_the_batch_loads_one_client_and_leaves_the_others_untouched(client):
    tenant, who = "meridian", _loader("meridian")
    cases_before = {t: len(client.get("/api/cases?status=all", headers=h(t)).json()) for t in CLIENTS}
    runs = client.post("/api/data/run", headers=h(tenant, who), json={}).json()["runs"]
    assert [r["source"] for r in runs] == [x["name"] for x in _overview(client, tenant)["sources"]]
    by = {r["source"]: r for r in runs}
    assert all(r["status"] in ("succeeded", "succeeded_with_rejects", "partial") for r in runs), [(r["source"], r["status"], r["error"]) for r in runs]
    assert sum(r["stats"]["loaded"] for r in runs) > 1000 and by["Core banking transactions"]["stats"]["objects_quarantined"] == 1
    assert all(r["started_by"] == next(p["name"] for p in PEOPLE[tenant]["people"] if p["id"] == who) for r in runs)

    after = _overview(client, tenant)
    assert len(after["runs"]) == 4
    ids = by["Core banking customer identifiers"]["stats"]
    assert ids["loaded"] == 40 and ids["rejected"] == 1  # the SSN with too few digits
    assert sum(x["waiting"] for x in after["sources"]) == 0  # the held file is not retried until it is replaced
    for other in (t for t in CLIENTS if t != tenant):
        o = _overview(client, other)
        assert o["runs"] == [] and all(x["waiting"] == x["objects"] for x in o["sources"])
        assert len(client.get("/api/cases?status=all", headers=h(other)).json()) == cases_before[other]
    assert len(client.get("/api/cases?status=all", headers=h(tenant)).json()) == cases_before[tenant] + txn_run_cases(runs)

    again = client.post("/api/data/run", headers=h(tenant, who), json={}).json()["runs"]  # nothing is loaded twice
    assert sum(r["stats"].get("loaded", 0) for r in again) == 0

    txn_run = by["Core banking transactions"]
    assert txn_run["stats"]["reconciled"] and txn_run["stats"]["cases"] > 0
    detail = client.get(f"/api/data/runs/{txn_run['id']}", headers=h(tenant)).json()
    assert detail["objects"] and {o["status"] for o in detail["objects"]} >= {"loaded", "quarantined"}
    assert next(o for o in detail["objects"] if o["status"] == "quarantined")["reason"]
    assert sum(o["transactions_now"] for o in detail["objects"]) == txn_run["stats"]["loaded"]
    test_running_the_batch_loads_one_client_and_leaves_the_others_untouched.run = txn_run["id"]


def test_a_run_and_a_source_belong_to_one_client(client):
    run_id = test_running_the_batch_loads_one_client_and_leaves_the_others_untouched.run
    source_id = _overview(client, "meridian")["sources"][0]["id"]
    for other in ("kestrel", "alder"):
        who = _loader(other)
        assert client.get(f"/api/data/runs/{run_id}", headers=h(other)).status_code == 404
        assert client.post(f"/api/data/sources/{source_id}/run", headers=h(other, who)).status_code == 404
        assert client.put(f"/api/data/sources/{source_id}", headers=h(other, who), json=SOURCE).status_code == 404
    assert _overview(client, "meridian")["sources"][0]["name"] != "Probe"


def test_a_loaded_transaction_traces_back_to_its_file(client):
    run_id = test_running_the_batch_loads_one_client_and_leaves_the_others_untouched.run
    loaded = next(o for o in client.get(f"/api/data/runs/{run_id}", headers=h("meridian")).json()["objects"] if o["status"] == "loaded")
    from sqlalchemy import select

    import app.db as appdb
    from app.domain.models import IngestedObject, Transaction

    with appdb.session_scope("meridian") as s:
        txn = s.scalars(select(Transaction).where(Transaction.feed_id == loaded["id"])).first()
        seeded = s.scalars(select(Transaction).where(Transaction.feed_id.not_in(select(IngestedObject.id)))).first()
    origin = client.get(f"/api/data/lineage/{txn.id}", headers=h("meridian")).json()["origin"]
    assert origin["key"] == loaded["key"] and origin["object"].endswith(loaded["key"]) and origin["run_id"] == run_id
    assert origin["source"] == "Core banking transactions" and origin["fingerprint"] == loaded["fingerprint"] and origin["mapping_version"] == 1
    assert client.get(f"/api/data/lineage/{txn.id}", headers=h("kestrel")).status_code == 404
    assert client.get("/api/data/lineage/no-such-transaction", headers=h("meridian")).status_code == 404
    older = client.get(f"/api/data/lineage/{seeded.id}", headers=h("meridian")).json()["origin"]  # seeded before any pipeline ran
    assert older["object"] is None and "outside a pipeline" in older["note"]


def test_only_the_operator_registers_a_clients_storage(client):
    home = (settings.lake_dir / "kestrel").resolve()
    good = {"locations": [{"id": "landing", "label": "Landing", "url": f"file://{home}/"},
                          {"id": "archive", "label": "Archive", "url": "s3://kestrel-archive/feeds/", "options": {"region_name": "eu-west-1"}}]}
    url = "/api/platform/tenants/kestrel/storage"
    assert client.put(url, json=good).status_code in (401, 403)
    assert client.put(url, headers={"X-Operator": "mallory"}, json=good).status_code == 403
    assert client.put(url, headers=h("kestrel", _loader("kestrel")), json=good).status_code in (401, 403)
    for bad in ([{"id": "landing", "url": f"file://{(settings.lake_dir / 'meridian').resolve()}/"}],  # another client's folder
                [{"id": "landing", "url": "file:///etc/"}], [{"id": "x", "url": "ftp://host/path/"}],
                [{"id": "x", "url": "s3://bucket/", "options": {"secret": "hunter2"}}], []):
        r = client.put(url, headers=OP, json={"locations": bad})
        assert r.status_code == 422, f"{bad}: {r.status_code}"
    out = client.put(url, headers=OP, json=good)
    assert out.status_code == 200, out.text
    assert [(x["id"], x["provider"]) for x in _overview(client, "kestrel")["locations"]] == [("landing", "Local directory"), ("archive", "Amazon S3 or S3-compatible")]
    assert "secret" not in out.text and [x["id"] for x in _overview(client, "meridian")["locations"]] == ["landing"]
    assert client.put("/api/platform/tenants/nowhere/storage", headers=OP, json=good).status_code in (404, 422)


def test_the_scheduler_runs_what_is_due_for_every_live_client_and_nothing_twice(client):
    from app.ingest import scheduler

    assert client.post("/api/platform/tenants/alder/status", headers=OP, json={"status": "suspended"}).status_code == 200
    try:
        started = scheduler.tick()
        assert "kestrel" in started and "alder" not in started  # a suspended client's pipelines wait
        runs = _overview(client, "kestrel")["runs"]
        assert len(runs) == started["kestrel"] and {r["trigger"] for r in runs} == {"schedule"}
        assert "kestrel" not in scheduler.tick()  # just run, so not due again
    finally:
        client.post("/api/platform/tenants/alder/status", headers=OP, json={"status": "live"})
    assert _overview(client, "alder")["runs"] == []
