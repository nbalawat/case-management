"""Starting, stopping and restarting stream jobs from the product.

Flink here is a stand-in that answers both REST interfaces the application uses: the SQL Gateway (sessions and
statements) and the cluster (job overview and cancel). tests/test_flink.py does the same through a real gateway.
"""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from sqlalchemy import select

from app.backbone import status
from app.config import settings
from app.domain import models as m
from app.streamjobs import control, flink, gateway
from app.streamjobs.control import ControlError
from app.streamjobs.gateway import GatewayError
from app.streamjobs.jobs import COMMON, JOBS, job_name, render
from support import T
from test_backbone import OTHER, world  # noqa: F401  fixture
from test_detection_governance import AUDRA, MAYA, OMAR, live


@pytest.fixture
def fake():
    """One server standing in for the gateway and the cluster. `.fail` makes the next statement containing it an error."""
    class Handler(BaseHTTPRequestHandler):
        def _send(self, body, code=200):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def do_GET(self):  # noqa: N802
            if self.path == "/info":
                return self._send({"productName": "Apache Flink", "version": "2.1.3"})
            if self.path == "/jobs/overview":
                return self._send({"jobs": box.jobs})
            op = self.path.split("/operations/")[1].split("/")[0]
            if self.path.endswith("/status"):
                return self._send({"status": "ERROR" if op in box.bad else "FINISHED"})
            if op in box.bad:
                return self._send({"errors": ["Internal server error.", "<Exception on server side:\norg.x.SqlGatewayException: Failed.\n\tat a.b(C.java:1)\n"
                                              "Caused by: org.apache.calcite.sql.validate.SqlValidatorException: Column 'nope' not found in any table\n\tat d.e(F.java:2)"]}, 500)
            return self._send({"resultKind": "SUCCESS"} | ({"jobID": box.started[op]} if op in box.started else {}))

        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if self.path == "/v1/sessions":
                box.sessions += 1
                box.name = ""
                return self._send({"sessionHandle": f"s{box.sessions}"})
            st = body["statement"]
            box.statements.append(st)
            op = f"op{len(box.statements)}"
            if box.fail and box.fail in st:
                box.bad.add(op)
            elif (named := re.match(r"SET 'pipeline.name' = '(.*)'$", st)):
                box.name = named.group(1)
            elif st.startswith("INSERT"):
                jid = f"{len(box.jobs) + 1:032x}"
                box.jobs.append({"jid": jid, "name": box.name, "state": "RUNNING", "start-time": 1_793_000_000_000 + len(box.jobs)})
                box.started[op] = jid
            self._send({"operationHandle": op})

        def do_DELETE(self):  # noqa: N802
            box.closed += 1
            self._send({"status": "CLOSED"})

        def do_PATCH(self):  # noqa: N802
            jid = self.path.split("/jobs/")[1].split("?")[0]
            for j in box.jobs:
                if j["jid"] == jid:
                    j["state"] = "CANCELED"
            self._send({})

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    box = server
    box.jobs, box.statements, box.started, box.bad, box.fail, box.sessions, box.closed, box.name = [], [], {}, set(), "", 0, 0, ""
    threading.Thread(target=server.serve_forever, daemon=True).start()
    old = settings.flink_url, settings.flink_gateway_url, settings.flink_kafka_bootstrap
    for k, v in (("flink_url", f"http://127.0.0.1:{server.server_port}"), ("flink_gateway_url", f"http://127.0.0.1:{server.server_port}"),
                 ("flink_kafka_bootstrap", "kafka:9092")):
        object.__setattr__(settings, k, v)
    yield box
    for k, v in zip(("flink_url", "flink_gateway_url", "flink_kafka_bootstrap"), old, strict=True):
        object.__setattr__(settings, k, v)
    server.shutdown()
    server.server_close()


def runs(s):
    return [(r.job, r.action, r.by) for r in s.scalars(select(m.StreamJobRun).order_by(m.StreamJobRun.at, m.StreamJobRun.id))]


def shown(s, job="structuring"):
    return next(j for j in status.view(s)["stream_jobs"]["jobs"] if j["job"] == job)


# ---- a rendered job as statements

@pytest.mark.parametrize("job", list(JOBS))
def test_a_job_is_sent_as_its_statements_without_comments_and_ends_with_the_insert(job):
    sts = gateway.statements(render(job, "acme-bank", bootstrap="kafka:9092"))
    assert sts[-1].startswith("INSERT INTO alerts_in") and sts[0] == f"SET 'pipeline.name' = 'caseforge {job} acme-bank'"
    assert not any("--" in s or ";" in s for s in sts) and sum(s.startswith("CREATE TEMPORARY TABLE") for s in sts) == 2


def test_no_template_keeps_a_comment_marker_inside_quoted_text():
    """Statements are cut at `--`, so a template must never put one inside a string."""
    for text in [COMMON, *(j.template for j in JOBS.values())]:
        for line in text.splitlines():
            assert line.partition("--")[0].count("'") % 2 == 0, line


# ---- start, stop, restart

def test_starting_a_job_submits_it_on_the_clients_settings_and_records_who(world, fake):  # noqa: F811
    with world.tenant(T) as s:
        live(s, params={"count": 4})
        run = control.start(s, "structuring", MAYA)
        assert (run.action, run.by, run.params["count"], run.engine_job_id) == ("started", "maya", 4, fake.jobs[0]["jid"])
        assert fake.jobs[0]["name"] == job_name("structuring", T) and "PATTERN (D{4})" in fake.statements[-1]
        assert any(f"'topic' = 'caseforge.{T}.transactions'" in st for st in fake.statements) and fake.sessions == fake.closed == 1
        assert any("'properties.bootstrap.servers' = 'kafka:9092'" in st for st in fake.statements)
        j = shown(s)
        assert (j["state"], j["started_by"], j["settings_changed"], j["engine_job_id"]) == ("RUNNING", "maya", False, run.engine_job_id)
        assert runs(s) == [("structuring", "started", "maya")]


def test_a_second_start_is_refused_while_one_is_running(world, fake):  # noqa: F811
    with world.tenant(T) as s:
        control.start(s, "structuring", MAYA)
        with pytest.raises(ControlError, match="already running"):
            control.start(s, "structuring", MAYA)
        assert len(fake.jobs) == 1 and len(runs(s)) == 1
        control.start(s, "rapid_movement", MAYA)  # another rule is another job
        assert [j["name"] for j in fake.jobs] == [job_name("structuring", T), job_name("rapid_movement", T)]


def test_stopping_cancels_the_job_on_the_cluster_and_records_it(world, fake):  # noqa: F811
    with world.tenant(T) as s:
        control.start(s, "structuring", MAYA)
        out = control.stop(s, "structuring", MAYA, "Moving this rule back into the application.")
        assert fake.jobs[0]["state"] == "CANCELED" and (out.action, out.note) == ("stopped", "Moving this rule back into the application.")
        assert shown(s)["state"] == "CANCELED" and shown(s)["started_by"] is None
        with pytest.raises(ControlError, match="not running"):
            control.stop(s, "structuring", MAYA)
        assert runs(s) == [("structuring", "started", "maya"), ("structuring", "stopped", "maya")]


def test_new_settings_are_flagged_on_the_running_job_and_a_restart_applies_them(world, fake):  # noqa: F811
    with world.tenant(T) as s:
        control.start(s, "structuring", MAYA)
        assert shown(s)["settings_changed"] is False
        live(s, params={"count": 5})
        assert shown(s)["settings_changed"] is True
        run = control.restart(s, "structuring", MAYA)
        assert [j["state"] for j in fake.jobs] == ["CANCELED", "RUNNING"] and "PATTERN (D{5})" in fake.statements[-1] and run.params["count"] == 5
        assert shown(s)["settings_changed"] is False and shown(s)["engine_job_id"] == fake.jobs[1]["jid"]
        assert [r[1] for r in runs(s)] == ["started", "stopped", "started"]


def test_a_restart_with_nothing_running_is_a_start(world, fake):  # noqa: F811
    with world.tenant(T) as s:
        control.restart(s, "high_risk_geo", MAYA)
        assert [j["name"] for j in fake.jobs] == [job_name("high_risk_geo", T)] and runs(s) == [("high_risk_geo", "started", "maya")]


def test_a_job_submitted_by_hand_is_shown_without_a_starter_and_can_still_be_stopped(world, fake):  # noqa: F811
    fake.jobs.append({"jid": "f" * 32, "name": job_name("structuring", T), "state": "RUNNING", "start-time": 1_793_000_000_000})
    with world.tenant(T) as s:
        j = shown(s)
        assert (j["state"], j["started_by"], j["settings_changed"]) == ("RUNNING", None, False)
        control.stop(s, "structuring", MAYA)
        assert fake.jobs[0]["state"] == "CANCELED"


def test_one_clients_controls_never_touch_anothers_job(world, fake):  # noqa: F811
    with world.tenant(OTHER) as s:
        control.start(s, "structuring", MAYA)
    with world.tenant(T) as s:
        assert shown(s)["state"] == "NOT SUBMITTED" and runs(s) == []
        with pytest.raises(ControlError, match="not running"):
            control.stop(s, "structuring", MAYA)
        control.start(s, "structuring", MAYA)
    assert sorted(j["name"] for j in fake.jobs) == sorted(job_name("structuring", t) for t in (T, OTHER)) and {j["state"] for j in fake.jobs} == {"RUNNING"}


# ---- who may, and when it cannot

@pytest.mark.parametrize("who", [OMAR, AUDRA])
def test_only_someone_who_can_approve_detection_changes_controls_a_job(world, fake, who):  # noqa: F811
    assert not who.can(control.PERMISSION) or who.read_only
    with world.tenant(T) as s:
        for act in (control.start, control.stop, control.restart):
            with pytest.raises(ControlError, match="needs the 'approve_tuning' permission"):
                act(s, "structuring", who)
        assert fake.statements == [] and runs(s) == [] and not control.may(who) and control.may(MAYA)


def test_without_a_gateway_jobs_are_not_controlled_from_the_product(world, fake):  # noqa: F811
    object.__setattr__(settings, "flink_gateway_url", "")
    with world.tenant(T) as s:
        with pytest.raises(ControlError, match="set FLINK_URL and FLINK_GATEWAY_URL"):
            control.start(s, "structuring", MAYA)
        assert status.view(s)["stream_jobs"]["controlled_here"] is False and not control.may(MAYA)


def test_an_unknown_job_is_not_found(world, fake):  # noqa: F811
    with world.tenant(T) as s, pytest.raises(ControlError, match="'velocity' not found"):
        control.start(s, "velocity", MAYA)


def test_flinks_own_reason_is_passed_on_and_nothing_is_recorded(world, fake):  # noqa: F811
    fake.fail = "INSERT"
    with world.tenant(T) as s:
        with pytest.raises(GatewayError, match="^Flink refused the job: Column 'nope' not found in any table$"):
            control.start(s, "structuring", MAYA)
        assert runs(s) == [] and fake.jobs == [] and fake.closed == 1  # the session is closed even so


def test_a_gateway_that_does_not_answer_is_said_plainly(world, fake):  # noqa: F811
    object.__setattr__(settings, "flink_gateway_url", "http://127.0.0.1:9")
    with world.tenant(T) as s:
        with pytest.raises(GatewayError, match="did not answer"):
            control.start(s, "structuring", MAYA)
        assert runs(s) == []
    assert gateway.available() is False


@pytest.mark.parametrize("url", ["flink-sql-gateway:8083", "file:///etc/passwd", ""])
def test_only_an_http_gateway_address_is_ever_opened(url):
    with pytest.raises(GatewayError, match="REST address"):
        gateway.submit("INSERT INTO a SELECT 1;", url=url or "x")


def test_a_cluster_that_is_down_stops_a_start_before_anything_is_submitted(world, fake):  # noqa: F811
    object.__setattr__(settings, "flink_url", "http://127.0.0.1:9")
    with world.tenant(T) as s, pytest.raises(flink.FlinkError, match="did not answer"):
        control.start(s, "structuring", MAYA)
    assert fake.statements == []


def test_a_job_id_that_is_not_one_is_never_put_in_an_address(fake):
    with pytest.raises(flink.FlinkError):
        flink.cancel("abc/../../x")


# ---- over HTTP, on the demo

def h(tenant, who):
    return {"X-Tenant": tenant, "X-Persona": who}


def test_the_api_starts_and_stops_a_job_for_those_allowed(client, fake):
    assert client.get("/api/backbone", headers=h("meridian", "maya")).json()["stream_jobs"]["can_control"] is True
    assert client.get("/api/backbone", headers=h("meridian", "omar")).json()["stream_jobs"]["can_control"] is False
    assert client.post("/api/stream-jobs/structuring/start", headers=h("meridian", "omar")).status_code == 403
    assert client.post("/api/stream-jobs/velocity/start", headers=h("meridian", "maya")).status_code == 404
    started = client.post("/api/stream-jobs/structuring/start", headers=h("meridian", "maya"))
    assert started.status_code == 200 and started.json()["action"] == "started" and started.json()["by"] == "maya"
    assert client.post("/api/stream-jobs/structuring/start", headers=h("meridian", "maya")).status_code == 409
    seen = next(j for j in client.get("/api/backbone", headers=h("meridian", "maya")).json()["stream_jobs"]["jobs"] if j["job"] == "structuring")
    assert seen["state"] == "RUNNING" and seen["started_by"] == "maya"
    assert client.post("/api/stream-jobs/structuring/restart", headers=h("meridian", "maya")).status_code == 200
    assert client.post("/api/stream-jobs/structuring/stop", headers=h("meridian", "maya"), json={"note": "Test over."}).json()["action"] == "stopped"
    assert client.post("/api/stream-jobs/structuring/stop", headers=h("meridian", "maya")).status_code == 409
    fake.fail = "INSERT"
    refused = client.post("/api/stream-jobs/rapid_movement/start", headers=h("meridian", "maya"))
    assert refused.status_code == 502 and "Column 'nope' not found" in refused.json()["detail"]
