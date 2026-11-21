"""Submitting a rendered job to Flink through its SQL Gateway, over REST.

The gateway is part of the Flink distribution (bin/sql-gateway.sh). A job goes in as its statements, one at a time,
in one session, and the last one (the INSERT) answers with the id of the job it started.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

from ..config import settings


CAUSE = re.compile(r"Caused by: [\w.$]+: (.+)")  # a Java cause line, without the exception's class name


class GatewayError(RuntimeError):
    pass


def statements(sql: str) -> list[str]:
    """A rendered job as the statements to run, in order, without its comments.

    Templates keep `--` out of quoted text and semicolons out of comments, so cutting each line at `--` is safe.
    """
    lines = [line.partition("--")[0].rstrip() for line in sql.splitlines()]
    return [st.strip() for st in "\n".join(lines).split(";") if st.strip()]


def _base(url: str | None) -> str:
    base = (url or settings.flink_gateway_url).rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise GatewayError("FLINK_GATEWAY_URL must be the SQL Gateway's REST address, for example http://flink-sql-gateway:8083")
    return base


def _call(method: str, url: str, body: dict | None = None, timeout: float = 10.0) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})  # noqa: S310  scheme checked in _base
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise GatewayError(_reason(e.read().decode(errors="replace"))) from None
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        raise GatewayError("The Flink SQL Gateway did not answer.") from e


def _reason(text: str) -> str:
    """The line of a Flink error worth showing a person: the innermost cause, not the Java stack."""
    try:
        text = "\n".join(json.loads(text).get("errors", [])) or text
    except ValueError:
        pass
    causes = [m.group(1) for line in text.splitlines() if (m := CAUSE.match(line))]
    first = next((line.strip() for line in text.splitlines() if line.strip() and not line.startswith(("\tat ", "<Exception"))), "")
    return ("Flink refused the job: " + (causes[-1] if causes else first or "no reason given"))[:500]


def available(url: str | None = None, timeout: float = 2.0) -> bool:
    try:
        return "version" in _call("GET", f"{_base(url)}/info", timeout=timeout)
    except GatewayError:
        return False


def submit(sql: str, url: str | None = None, wait: float = 90.0) -> str:
    """Run a rendered job's statements and return the id of the job its INSERT started."""
    base, todo = _base(url), statements(sql)
    if not todo or not todo[-1].upper().startswith("INSERT"):
        raise GatewayError("A stream job must end with its INSERT.")
    session = _call("POST", f"{base}/v1/sessions", {})["sessionHandle"]
    here, job_id = f"{base}/v1/sessions/{session}", ""
    try:
        end = time.monotonic() + wait
        for st in todo:
            op = f"{here}/operations/{_call('POST', f'{here}/statements', {'statement': st})['operationHandle']}"
            while (state := _call("GET", f"{op}/status").get("status")) not in ("FINISHED", "ERROR", "CANCELED", "TIMEOUT", "CLOSED"):
                if time.monotonic() > end:
                    raise GatewayError("Flink took too long to accept the job.")
                time.sleep(0.2)
            result = _call("GET", f"{op}/result/0")  # on an error this raises with Flink's reason
            if state != "FINISHED":
                raise GatewayError(f"Flink ended a statement as {state}.")
            job_id = str(result.get("jobID") or "") or job_id
    finally:
        try:
            _call("DELETE", here)
        except GatewayError:
            pass  # the session times out on its own
    if not job_id:
        raise GatewayError("Flink accepted the statements but started no job.")
    return job_id
