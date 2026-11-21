"""Stream jobs: rendering one for a client, and what the platform reports about the cluster running them.

The cluster here is a stand-in that answers Flink's REST endpoint. tests/test_flink.py runs the rendered job on a
real one.
"""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.backbone import status
from app.config import settings
from app.streamjobs import flink
from app.streamjobs.__main__ import main
from app.streamjobs.jobs import JOBS, JobError, bootstrap_of, job_name, render
from support import T
from test_backbone import world  # noqa: F401  fixture
from test_detection_governance import live

BROKERS = "kafka:9092"


@pytest.fixture
def cluster():
    """A stand-in for a Flink cluster's REST API. Set `.jobs` to what it should report."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            box.paths.append(self.path)
            body = json.dumps({"jobs": box.jobs}).encode() if box.jobs is not None else b"not json"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    box = server
    box.jobs, box.paths = [], []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    old = settings.flink_url
    object.__setattr__(settings, "flink_url", f"http://127.0.0.1:{server.server_port}/")
    yield box
    object.__setattr__(settings, "flink_url", old)
    server.shutdown()
    server.server_close()


def run(name, state="RUNNING", start=1_793_000_000_000):
    return {"jid": "a" * 32, "name": name, "state": state, "start-time": start}


# ---- rendering

def test_a_job_is_rendered_onto_the_clients_own_topics():
    sql = render("structuring", "acme-bank", bootstrap=BROKERS)
    assert "'topic' = 'caseforge.acme-bank.transactions'" in sql and "'topic' = 'caseforge.acme-bank.alerts-in'" in sql
    assert "'properties.bootstrap.servers' = 'kafka:9092'" in sql and "'pipeline.name' = 'caseforge structuring acme-bank'" in sql
    assert "'properties.group.id' = 'caseforge-flink-structuring-acme-bank'" in sql
    assert "${" not in sql


def test_two_clients_never_share_a_topic_a_group_or_a_job_name():
    a, b = (render("structuring", t, bootstrap=BROKERS) for t in ("acme-bank", "kestrel"))
    quoted = lambda sql: set(re.findall(r"= '([^']*)'", sql))  # noqa: E731
    shared = quoted(a) & quoted(b)
    assert not any("acme" in v or "kestrel" in v for v in shared)
    assert {v for v in quoted(a) if "acme-bank" in v} == {"caseforge structuring acme-bank", "caseforge.acme-bank.transactions",
                                                          "caseforge-flink-structuring-acme-bank", "caseforge.acme-bank.alerts-in"}


def test_the_shipped_thresholds_are_the_scenarios_own():
    sql = render("structuring", "acme-bank", bootstrap=BROKERS)
    assert "amount BETWEEN 8000 AND 9999.99" in sql and "PATTERN (D{3}) WITHIN INTERVAL '7' DAY" in sql
    assert "between $8,000 and $10,000 within 7 days" in sql


def test_a_clients_approved_thresholds_are_written_into_its_job():
    sql = render("structuring", "acme-bank", {"min": 5000, "count": 4, "days": 10}, BROKERS)
    assert "amount BETWEEN 5000 AND 9999.99" in sql and "PATTERN (D{4}) WITHIN INTERVAL '10' DAY" in sql
    assert "between $5,000 and $10,000 within 10 days" in sql


def test_rapid_movement_is_rendered_with_the_scenarios_own_settings_and_a_clients():
    sql = render("rapid_movement", "acme-bank", bootstrap=BROKERS)
    assert "A.amount >= 20000," in sql and "< 0.8 * A.amount" in sql and "PATTERN (A B* C) WITHIN INTERVAL '48' HOUR" in sql
    assert "'pipeline.name' = 'caseforge rapid_movement acme-bank'" in sql and "'caseforge-flink-rapid-movement-acme-bank'" in sql
    assert "'flink-rapid-movement'," in sql and "${" not in sql
    sql = render("rapid_movement", "acme-bank", {"min_in": 5000, "out_ratio": 0.5, "hours": 12}, BROKERS)
    assert "A.amount >= 5000," in sql and ">= 0.5 * A.amount" in sql and "INTERVAL '12' HOUR" in sql and "sent out within 12 hours" in sql


def test_high_risk_wires_are_rendered_with_the_platforms_country_list_and_a_clients_threshold():
    from app.detection.scenarios import HIGH_RISK_COUNTRIES

    sql = render("high_risk_geo", "acme-bank", bootstrap=BROKERS)
    assert "counterparty_country IN ('AF', 'IR', 'KP', 'MM', 'SY')" in sql and sql.count("10000") == 3 and "'flink-high-risk-geo'," in sql
    assert set(re.findall(r"'([A-Z]{2})'", sql)) == HIGH_RISK_COUNTRIES and "${" not in sql
    assert "total >= 2500.5 AND total - amount < 2500.5" in render("high_risk_geo", "acme-bank", {"min_total": 2500.5}, BROKERS)


@pytest.mark.parametrize(("job", "params"), [
    ("rapid_movement", {"min_in": "1 OR 1=1"}), ("rapid_movement", {"min_in": 0}), ("rapid_movement", {"out_ratio": 0}), ("rapid_movement", {"out_ratio": 1.5}),
    ("rapid_movement", {"hours": 0}), ("rapid_movement", {"hours": 9000}), ("rapid_movement", {"hours": "48' HOUR --"}), ("rapid_movement", {"out_ratio": None}),
    ("rapid_movement", {"min_in": float("inf")}), ("rapid_movement", {"min_in": float("nan")}),
    ("high_risk_geo", {"min_total": "x"}), ("high_risk_geo", {"min_total": -1}), ("high_risk_geo", {"min_total": float("nan")}), ("high_risk_geo", {"min_total": []})])
def test_the_other_jobs_refuse_settings_that_are_not_sane_numbers(job, params):
    with pytest.raises(JobError, match="settings"):
        render(job, "acme-bank", params, BROKERS)


def test_a_country_code_that_could_change_the_sql_is_refused(monkeypatch):
    from app.streamjobs import jobs

    monkeypatch.setattr(jobs, "HIGH_RISK_COUNTRIES", {"IR", "X') OR ('1'='1"})
    with pytest.raises(JobError, match="settings"):
        render("high_risk_geo", "acme-bank", bootstrap=BROKERS)


@pytest.mark.parametrize("job", list(JOBS))
def test_every_job_shares_one_definition_of_the_topics_and_fills_every_placeholder(job):
    sql = render(job, "acme-bank", bootstrap=BROKERS)
    assert sql.count("CREATE TEMPORARY TABLE") == 2 and sql.count("INSERT INTO alerts_in") == 1 and "${" not in sql
    assert "'topic' = 'caseforge.acme-bank.transactions'" in sql and "'topic' = 'caseforge.acme-bank.alerts-in'" in sql
    assert f"'{JOBS[job].scenario}'," in sql and f"'{JOBS[job].source}'," in sql


# ---- name screening: the client's list is written into the job

AWKWARD = ["O'Brien & Sons; Ltd", "Semi;colon -- Holdings", "${BOOTSTRAP} Trading", "Zürich Über 中文 𝒜G", "Back\\slash\nNewline"]


def test_name_screening_is_the_fourth_job_and_renders_on_the_starting_list():
    assert list(JOBS) == ["structuring", "rapid_movement", "high_risk_geo", "sanctions_name"]
    sql = render("sanctions_name", "acme-bank", bootstrap=BROKERS)
    assert "CREATE TEMPORARY FUNCTION WATCH_MATCH AS 'caseforge.WatchMatch';" in sql and "CAST(0.86 AS DOUBLE)" in sql
    assert "ARRAY[U&'Volkov Maritime Trading', U&'Northgate Petrochem FZE', U&'Darya Shipping Lines', U&'Zarin Exchange House'], ARRAY['']" in sql


def test_a_listed_name_can_hold_nothing_but_itself_in_the_sql():
    from app.streamjobs import gateway
    from app.streamjobs.jobs import quoted

    assert quoted("O'Brien; -- ${X}") == "U&'O\\0027Brien\\003B \\002D\\002D \\0024\\007BX\\007D'" and quoted("𝒜") == "U&'\\D835\\DC9C'"
    plain = gateway.statements(render("sanctions_name", "acme-bank", bootstrap=BROKERS))
    sql = render("sanctions_name", "acme-bank", {"names": [[n, f"e{i}"] for i, n in enumerate(AWKWARD)], "exempt": [["o'brien; x -- y", "e0"]]}, BROKERS)
    sts = gateway.statements(sql)
    assert len(sts) == len(plain) and "${" not in sql
    array = sts[-1].split("WATCH_MATCH(counterparty, ")[1].split(", CAST(")[0]
    assert re.fullmatch(r"ARRAY\[(U&'[A-Za-z0-9 \\]*'(, )?)+\], ARRAY\[(U&'[A-Za-z0-9 \\]*'(, )?)+\]", array), array
    assert array.count("U&'") == len(AWKWARD) + 1


def test_a_pair_ruled_out_is_written_against_every_name_of_that_entry_and_no_entry_id_reaches_the_job():
    sql = render("sanctions_name", "acme-bank", {"names": [["Alpha Co", "wle_1"], ["Beta Shipping", "wle_2"], ["Beta Ship", "wle_2"]],
                                                 "exempt": [["beta shipping gmbh", "wle_2"], ["someone else", "wle_9"]], "threshold": 0.9}, BROKERS)
    assert "ARRAY[U&'1\\003Abeta shipping gmbh', U&'2\\003Abeta shipping gmbh'], CAST(0.9 AS DOUBLE)" in sql and "wle_" not in sql
    assert "3 names and 2 pairs ruled out" in sql and "at least 90% alike" in sql


@pytest.mark.parametrize("params", [
    {"names": []}, {"names": [["", "e"]]}, {"names": [["x" * 201, "e"]]}, {"names": [["ok", "e"]] * 5001}, {"names": "Volkov"}, {"names": [["only one"]]},
    {"threshold": 0}, {"threshold": 1.5}, {"threshold": "0.9 OR 1=1"}, {"threshold": None}, {"exempt": [["", "e"]]}, {"exempt": [["k" * 201, "e"]]}])
def test_name_screening_refuses_a_list_or_threshold_it_cannot_run(params):
    with pytest.raises(JobError, match="settings"):
        render("sanctions_name", "acme-bank", params, BROKERS)


def test_what_is_recorded_of_a_list_is_its_size_and_a_fingerprint_that_follows_every_change():
    summary = JOBS["sanctions_name"].summary
    base = {"threshold": 0.86, "names": [["Alpha Co", "e1"], ["Beta Shipping", "e2"]], "exempt": []}
    first = summary(base)
    assert first == {"threshold": 0.86, "names": 2, "ruled_out": 0, "list_fingerprint": first["list_fingerprint"]} and len(first["list_fingerprint"]) == 16
    assert summary(base | {"exempt": [["beta shipping gmbh", "e2"]]})["list_fingerprint"] != first["list_fingerprint"]
    assert summary(base | {"names": [["Alpha Co", "e1"], ["Beta Shiping", "e2"]]})["list_fingerprint"] != first["list_fingerprint"]
    assert summary(dict(base)) == first and JOBS["structuring"].summary({"count": 3}) == {"count": 3}


def test_the_topic_prefix_follows_the_platforms():
    old = settings.event_topic_prefix
    object.__setattr__(settings, "event_topic_prefix", "uat")
    try:
        sql = render("structuring", "acme-bank", bootstrap=BROKERS)
    finally:
        object.__setattr__(settings, "event_topic_prefix", old)
    assert "'uat.acme-bank.transactions'" in sql and "'uat structuring acme-bank'" in sql and "caseforge" not in sql.split("SET")[1]


def test_the_brokers_come_from_the_event_bus_when_not_given():
    old = settings.event_bus_url
    object.__setattr__(settings, "event_bus_url", "kafka://b1:9092,b2:9092")
    try:
        assert "'properties.bootstrap.servers' = 'b1:9092,b2:9092'" in render("structuring", "acme-bank")
    finally:
        object.__setattr__(settings, "event_bus_url", old)


@pytest.mark.parametrize("url", ["", "memory://", "http://kafka:9092"])
def test_without_a_kafka_bus_there_is_nothing_to_render_against(url):
    with pytest.raises(JobError, match="reads Kafka"):
        bootstrap_of(url)


@pytest.mark.parametrize("tenant", ["", "A", "acme'; DROP TABLE x; --", "acme bank", "acme.bank", "Acme", "ac", "a" * 40, "acme\nbank", "acme-bank\n", "${TENANT}"])
def test_a_client_id_that_could_change_the_sql_is_refused(tenant):
    with pytest.raises(JobError, match="not a client id"):
        render("structuring", tenant, bootstrap=BROKERS)


@pytest.mark.parametrize("brokers", ["kafka", "kafka:9092'", "kafka:9092, other:9092", "kafka:9092;x", "kafka:port", "'", "kafka:9092\n"])
def test_brokers_that_could_change_the_sql_are_refused(brokers):
    with pytest.raises(JobError, match="host:port"):
        render("structuring", "acme-bank", bootstrap=brokers)


@pytest.mark.parametrize("params", [{"min": "8000 OR 1=1"}, {"count": "3}) --"}, {"days": None}, {"min": 0}, {"min": -5}, {"count": 1}, {"count": 500},
                                    {"days": 0}, {"days": 400}, {"min": 20000}])
def test_thresholds_that_are_not_sane_numbers_are_refused(params):
    with pytest.raises(JobError, match="settings"):
        render("structuring", "acme-bank", params, BROKERS)


def test_a_prefix_that_could_change_the_sql_is_refused():
    old = settings.event_topic_prefix
    object.__setattr__(settings, "event_topic_prefix", "case'forge")
    try:
        with pytest.raises(JobError, match="EVENT_TOPIC_PREFIX"):
            render("structuring", "acme-bank", bootstrap=BROKERS)
    finally:
        object.__setattr__(settings, "event_topic_prefix", old)


def test_an_unknown_job_is_named_as_such():
    with pytest.raises(JobError, match="No stream job called 'velocity'"):
        render("velocity", "acme-bank", bootstrap=BROKERS)


def test_every_job_has_a_template_and_a_real_scenario_behind_it():
    from app.detection.scenarios import REGISTRY

    for j in JOBS.values():
        assert j.scenario in REGISTRY and "INSERT INTO alerts_in" in j.template and len(j.source) <= 40


def test_no_comment_in_a_template_holds_a_semicolon():
    """Flink's SQL client splits statements on semicolons, comments included. One in a comment breaks the job."""
    for j in JOBS.values():
        for line in j.template.splitlines():
            assert ";" not in line.partition("--")[2], line


def test_the_alert_a_job_writes_has_every_field_the_alert_stream_requires():
    from app.backbone.streams import ALERT_REQUIRED

    table = render("structuring", "acme-bank", bootstrap=BROKERS).split("CREATE TEMPORARY TABLE alerts_in (")[1].split(") WITH")[0]
    columns = [line.split()[0] for line in table.strip().splitlines()]
    assert set(ALERT_REQUIRED) <= set(columns) and "txn_ids ARRAY<STRING>" in table


# ---- the command

def test_the_command_prints_the_job(capsys):
    assert main(["structuring", "acme-bank", "--defaults", "--bootstrap", BROKERS]) == 0
    out = capsys.readouterr().out
    assert out == render("structuring", "acme-bank", bootstrap=BROKERS)


def test_the_command_lists_the_jobs(capsys):
    assert main(["--list"]) == 0 and capsys.readouterr().out.startswith("structuring\t")


@pytest.mark.parametrize("argv", [[], ["structuring"], ["velocity", "acme-bank", "--defaults"], ["structuring", "Acme!", "--defaults", "--bootstrap", BROKERS],
                                  ["structuring", "acme-bank", "--defaults", "--bootstrap", "nope"]])
def test_the_command_refuses_what_it_cannot_render_and_prints_no_sql(argv, capsys):
    assert main(argv) == 2
    io = capsys.readouterr()
    assert io.out == "" and io.err


def test_the_command_reads_a_clients_own_thresholds(world, capsys, monkeypatch):  # noqa: F811
    import app.db as appdb
    from app.backbone import worker

    monkeypatch.setattr(worker, "reload_clients", lambda: None)
    monkeypatch.setattr(appdb, "ensure_schema", lambda: None)
    assert main(["structuring", T, "--bootstrap", BROKERS]) == 0
    io = capsys.readouterr()
    assert "PATTERN (D{3})" in io.out and "also switched on in the application" in io.err
    with world.tenant(T) as s:
        live(s, params={"count": 4})
        s.commit()
    assert main(["structuring", T, "--bootstrap", BROKERS]) == 0
    assert "PATTERN (D{4})" in capsys.readouterr().out
    with world.tenant(T) as s:
        live(s, params={"count": 4}, enabled=False, note="Flink raises this rule for this client from now on.")
        s.commit()
    assert main(["structuring", T, "--bootstrap", BROKERS]) == 0
    assert "switched off in the application" in capsys.readouterr().err


def test_the_command_says_so_when_the_client_does_not_exist(world, capsys, monkeypatch):  # noqa: F811
    import app.db as appdb
    from app.backbone import worker

    monkeypatch.setattr(worker, "reload_clients", lambda: None)
    monkeypatch.setattr(appdb, "ensure_schema", lambda: None)
    assert main(["structuring", "nobody-here", "--bootstrap", BROKERS]) == 2
    io = capsys.readouterr()
    assert io.out == "" and io.err.strip() == "There is no client called 'nobody-here'."


# ---- what the platform reports

def test_with_no_cluster_configured_the_page_says_where_scenarios_run(world):  # noqa: F811
    assert settings.flink_url == ""
    with world.tenant(T) as s:
        v = status.view(s)["stream_jobs"]
    assert v == {"engine": "none", "connected": False, "jobs": [], "controlled_here": False,
                 "note": "No stream processor is connected. Scenarios run in the application's stream consumer."}


def test_a_job_that_was_never_submitted_is_reported_as_such(cluster):
    v = flink.status(T)
    assert v["connected"] and v["engine"] == "flink"
    assert [(j["job"], j["state"], j["since"], j["also_in_application"]) for j in v["jobs"]] == [
        ("structuring", "NOT SUBMITTED", None, False), ("rapid_movement", "NOT SUBMITTED", None, False), ("high_risk_geo", "NOT SUBMITTED", None, False),
        ("sanctions_name", "NOT SUBMITTED", None, False)]
    assert cluster.paths == ["/jobs/overview"]


def test_a_running_job_is_found_by_its_name(cluster):
    cluster.jobs = [run(job_name("structuring", T))]
    j, *others = flink.status(T)["jobs"]
    assert {o["state"] for o in others} == {"NOT SUBMITTED"}
    assert j["state"] == "RUNNING" and j["since"].startswith("2026-") and j["source"] == "flink-structuring"


def test_one_client_never_sees_anothers_job(cluster):
    cluster.jobs = [run(job_name("structuring", "kestrel")), run("somebody else's job"), run(job_name("structuring", T) + "x")]
    assert flink.status(T)["jobs"][0]["state"] == "NOT SUBMITTED"
    assert flink.status("kestrel")["jobs"][0]["state"] == "RUNNING"


def test_a_running_job_wins_over_older_cancelled_runs_of_it(cluster):
    name = job_name("structuring", T)
    cluster.jobs = [run(name, "CANCELED", 3_000), run(name, "RUNNING", 2_000), run(name, "FAILED", 1_000)]
    assert flink.status(T)["jobs"][0]["state"] == "RUNNING"


def test_with_nothing_running_the_latest_run_is_shown(cluster):
    name = job_name("structuring", T)
    cluster.jobs = [run(name, "CANCELED", 1_000), run(name, "FAILED", 3_000), run(name, "FINISHED", 2_000)]
    assert flink.status(T)["jobs"][0]["state"] == "FAILED"


def test_a_rule_running_in_both_engines_is_called_out(cluster):
    cluster.jobs = [run(job_name("structuring", T))]
    assert flink.status(T, {"structuring": True})["jobs"][0]["also_in_application"] is True
    assert flink.status(T, {"structuring": False})["jobs"][0]["also_in_application"] is False
    cluster.jobs = [run(job_name("structuring", T), "FAILED")]
    assert flink.status(T, {"structuring": True})["jobs"][0]["also_in_application"] is False  # the job is not raising anything


def test_the_view_knows_whether_the_application_raises_the_rule_too(world, cluster):  # noqa: F811
    cluster.jobs = [run(job_name("structuring", T))]
    with world.tenant(T) as s:
        assert status.view(s)["stream_jobs"]["jobs"][0]["also_in_application"] is True
        live(s, enabled=False, note="Flink raises this rule for this client from now on.")
        assert status.view(s)["stream_jobs"]["jobs"][0]["also_in_application"] is False


def test_a_cluster_that_is_down_is_reported_not_raised(world):  # noqa: F811
    object.__setattr__(settings, "flink_url", "http://127.0.0.1:9")  # nothing listens on the discard port
    try:
        with world.tenant(T) as s:
            v = status.view(s)["stream_jobs"]
    finally:
        object.__setattr__(settings, "flink_url", "")
    assert v == {"engine": "flink", "connected": False, "note": "The Flink cluster did not answer.", "jobs": [], "controlled_here": False}


def test_a_cluster_that_answers_nonsense_is_reported_not_raised(cluster):
    cluster.jobs = None
    v = flink.status(T)
    assert not v["connected"] and v["note"] == "The Flink cluster did not answer."


@pytest.mark.parametrize("url", ["flink-jobmanager:8081", "file:///etc/passwd", "ftp://host/x"])
def test_only_an_http_address_is_ever_opened(url):
    object.__setattr__(settings, "flink_url", url)
    try:
        v = flink.status(T)
    finally:
        object.__setattr__(settings, "flink_url", "")
    assert not v["connected"] and "REST address" in v["note"]


def test_the_api_shows_each_client_its_stream_jobs(client, cluster):
    cluster.jobs = [run(job_name("structuring", "meridian"))]
    states = {}
    for t in client.get("/api/tenants").json():
        r = client.get("/api/backbone", headers={"X-Tenant": t["id"], "X-Persona": t["default_person"]})
        assert r.status_code == 200
        states[t["id"]] = r.json()["stream_jobs"]["jobs"][0]["state"]
    assert states.pop("meridian") == "RUNNING" and states and set(states.values()) == {"NOT SUBMITTED"}
