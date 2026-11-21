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
out = {}
with TestClient(app) as c:  # startup creates the schema and seeds every demo client, exactly as the container does
    for t in c.get('/api/tenants').json():
        h = {'X-Tenant': t['id'], 'X-Persona': t['default_person']}
        cases = c.get('/api/cases?status=all', headers=h)
        work = c.get('/api/work', headers=h)
        first = c.get(f"/api/cases/{cases.json()[0]['id']}", headers=h)
        out[t['id']] = {'cases': len(cases.json()), 'codes': [cases.status_code, work.status_code, first.status_code]}
print('RESULT ' + json.dumps(out))
"""


def test_the_demo_starts_and_answers_on_postgres():
    env = {**os.environ, "DATABASE_URL": URL, "PIPELINE_SCHEDULER": "0"}
    run = subprocess.run([sys.executable, "-c", SCRIPT], cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, timeout=600)
    assert run.returncode == 0, run.stderr[-3000:]
    out = json.loads(next(line for line in run.stdout.splitlines() if line.startswith("RESULT ")).removeprefix("RESULT "))
    assert set(out) == {"meridian", "kestrel", "alder"}
    for tenant, got in out.items():
        assert got["codes"] == [200, 200, 200], (tenant, got)
        assert got["cases"] > 0, (tenant, got)
