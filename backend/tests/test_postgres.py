"""The whole demo on a real Postgres. SQLite forgives things Postgres does not, so this is the test that speaks for `docker compose up`.

Runs when TEST_POSTGRES_URL names an empty, disposable database, for example
TEST_POSTGRES_URL=postgresql+psycopg://caseforge@127.0.0.1:5432/caseforge_test. Everything in that database is dropped.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

URL = os.getenv("TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(not URL.startswith("postgresql"), reason="set TEST_POSTGRES_URL to a disposable Postgres database to run")

SCRIPT = """
import json
from fastapi.testclient import TestClient
from app.main import app  # importing the app registers every table
from app.db import Base, engine
Base.metadata.drop_all(engine)
with engine.begin() as c:
    c.exec_driver_sql('DROP TABLE IF EXISTS alembic_version')  # so startup builds the schema by running every migration
out = {}
with TestClient(app) as c:  # startup creates the schema and seeds every demo client, exactly as the container does
    for t in c.get('/api/tenants').json():
        h = {'X-Tenant': t['id'], 'X-Persona': t['default_person']}
        cases = c.get('/api/cases?status=all', headers=h)
        desk = c.get('/api/entities', headers=h)
        hit = c.get('/api/search?q=a', headers=h)
        first = c.get(f"/api/cases/{cases.json()[0]['id']}", headers=h)
        out[t['id']] = {'cases': len(cases.json()), 'counterparties': desk.json()['counts']['counterparties'],
                        'codes': [cases.status_code, desk.status_code, hit.status_code, first.status_code]}
    # The outbox relay on Postgres: rows are taken with FOR UPDATE SKIP LOCKED, published in id order and marked.
    from sqlalchemy import func, select
    from app.backbone import relay
    from app.backbone.bus import MemoryBus
    from app.db import session_scope
    from app.domain.models import OutboxEvent
    bus = MemoryBus()
    with session_scope(platform=True) as s:
        rows = s.scalar(select(func.count()).select_from(OutboxEvent))
        ids = list(s.scalars(select(OutboxEvent.event_id).order_by(OutboxEvent.id)))
    sent = relay.drain(bus, batch=200)
    with session_scope(platform=True) as s:
        waiting = s.scalar(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.published_at.is_(None)))
    on_bus = [m.headers['id'] for t in bus.topics() for m in bus.read(t)]
    out['_outbox'] = {'rows': rows, 'sent': sent, 'waiting': waiting, 'on_bus': len(on_bus), 'same': sorted(on_bus) == sorted(ids), 'again': relay.drain(bus)}
print('RESULT ' + json.dumps(out))
"""


def test_the_demo_starts_and_answers_on_postgres():
    env = {**os.environ, "DATABASE_URL": URL, "PIPELINE_SCHEDULER": "0"}
    run = subprocess.run([sys.executable, "-c", SCRIPT], cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, timeout=600)
    assert run.returncode == 0, run.stderr[-3000:]
    out = json.loads(next(line for line in run.stdout.splitlines() if line.startswith("RESULT ")).removeprefix("RESULT "))
    box = out.pop("_outbox")
    assert box["rows"] > 1000 and box["sent"] == box["rows"] == box["on_bus"] and box["same"] and box["waiting"] == 0 and box["again"] == 0, box
    assert set(out) == {"meridian", "kestrel", "alder"}
    for tenant, got in out.items():
        assert got["codes"] == [200, 200, 200, 200], (tenant, got)
        assert got["cases"] > 0 and got["counterparties"] > 0, (tenant, got)


MIGRATIONS = """
import json, threading
from sqlalchemy import text
from app import migrations
from app.db import Base, engine, ensure_schema
from app.domain import models
with engine.begin() as c:
    Base.metadata.drop_all(c)
    c.exec_driver_sql('DROP TABLE IF EXISTS alembic_version')
    c.exec_driver_sql('DROP TABLE IF EXISTS schema_version')
errors = []
def start():
    try:
        ensure_schema()
    except Exception as e:
        errors.append(repr(e))
pods = [threading.Thread(target=start) for _ in range(4)]  # four pods starting against an empty database at once
[t.start() for t in pods]; [t.join() for t in pods]
with engine.connect() as c:
    at = migrations.current(c)
print('RESULT ' + json.dumps({'errors': errors, 'at': at, 'head': migrations.head(), 'drift': [str(d) for d in migrations.drift(engine)]}))
"""


def test_migrations_build_the_schema_the_models_describe_and_pods_starting_together_do_not_collide():
    env = {**os.environ, "DATABASE_URL": URL}
    run = subprocess.run([sys.executable, "-c", MIGRATIONS], cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stderr[-3000:]
    out = json.loads(next(line for line in run.stdout.splitlines() if line.startswith("RESULT ")).removeprefix("RESULT "))
    assert out["errors"] == [] and out["at"] == out["head"] and out["drift"] == [], out
