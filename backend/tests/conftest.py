import os

os.environ.setdefault("CASEFORGE_NO_DOTENV", "1")  # a developer's own .env (an API key, say) never changes what the tests see

from contextlib import contextmanager

import pytest
from sqlalchemy.orm import sessionmaker

from app.db import Base, make_engine
from app.tenancy import bind_tenant
from app.tenants import registry
from app.tenants.onboarding import onboard
from support import T, TEMPLATE


@pytest.fixture(autouse=True, scope="session")
def _storage(tmp_path_factory):
    """Tests never touch the real lake: client landing zones and the quarantine area live in a temporary directory."""
    from app.config import settings

    base = tmp_path_factory.mktemp("storage")
    object.__setattr__(settings, "lake_dir", base / "lake")
    object.__setattr__(settings, "quarantine_url", f"file://{base / 'quarantine'}")
    object.__setattr__(settings, "evidence_url", f"file://{base / 'evidence'}")
    object.__setattr__(settings, "exports_url", f"file://{base / 'exports'}")
    object.__setattr__(settings, "backup_url", f"file://{base / 'backups'}")
    object.__setattr__(settings, "keys_dir", base / "keys")  # each run gets its own keyring and master key
    object.__setattr__(settings, "allow_memory_storage", True)
    yield base


class Db:
    """An empty in-memory platform. Sessions are opened the way the application opens them."""

    def __init__(self):
        self.engine = make_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    @contextmanager
    def tenant(self, tenant_id: str):
        with self.factory() as session:
            yield bind_tenant(session, tenant_id)

    @contextmanager
    def platform(self):
        with self.factory() as session:
            session.info["platform"] = True
            yield session

    def onboard(self, tenant_id: str, template: str = TEMPLATE, **kw):
        with self.platform() as p:
            rt = onboard(p, tenant_id=tenant_id, name=kw.pop("name", tenant_id.title()), template=template, **kw)
            p.commit()
        return rt


@pytest.fixture
def db():
    saved = registry.swap({})
    yield Db()
    registry.swap(saved)


@pytest.fixture
def s(db):
    """A session for the reference client."""
    db.onboard(T)
    with db.tenant(T) as session:
        yield session


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """The whole application over HTTP, seeded with the three demo clients."""
    from fastapi.testclient import TestClient

    import app.db as appdb
    import app.seed as seed_module
    from app.config import settings
    from app.main import app

    eng = appdb.make_engine(f"sqlite:///{tmp_path_factory.mktemp('db') / 'test.db'}")
    old = appdb.engine, appdb.SessionLocal, settings.samples_dir
    appdb.engine, appdb.SessionLocal = eng, sessionmaker(bind=eng, expire_on_commit=False)
    seed_module.engine = eng
    object.__setattr__(settings, "samples_dir", tmp_path_factory.mktemp("samples"))
    saved = registry.swap({})
    previous = _engine_for_the_app()
    with TestClient(app) as c:
        yield c
    _restore_engine(previous)
    registry.swap(saved)
    appdb.engine, appdb.SessionLocal = old[0], old[1]
    seed_module.engine = old[0]
    object.__setattr__(settings, "samples_dir", old[2])


# ---- the whole suite on the Temporal engine -------------------------------------------------------------------------
# TEST_WORKFLOW_ENGINE=temporal TEST_TEMPORAL_URL=temporal://127.0.0.1:7233 pytest
# runs the suite with case workflows on a real Temporal server: every test that drives the application over HTTP (the
# `client` fixture) has each person's step become an update to that case's workflow, applied by a worker running
# beside the tests. Tests that call the case service on an in-memory database test the rules themselves, which are
# the same on both engines, and run as they always do. Unset, everything runs on the built-in engine.

@pytest.fixture(autouse=True, scope="session")
def _workflow_engine():
    import os

    wanted = os.getenv("TEST_WORKFLOW_ENGINE", "builtin")
    url = os.getenv("TEST_TEMPORAL_URL", "")
    if wanted != "temporal":
        yield "builtin"
        return
    if not url.startswith("temporal://"):
        pytest.exit("TEST_WORKFLOW_ENGINE=temporal needs TEST_TEMPORAL_URL", returncode=2)
    import uuid

    from app.config import settings
    from app.durable.engine import TemporalEngine

    run = uuid.uuid4().hex[:8]
    saved = {k: getattr(settings, k) for k in ("temporal_url", "workflow_engine", "temporal_id_prefix", "temporal_task_queue", "temporal_sync_seconds",
                                               "temporal_namespace_mode", "temporal_namespace_prefix", "temporal_schedules")}
    # No schedules: a sweep or pipeline run firing in the middle of a test would change what it counts. Tests that
    # want them (tests/test_temporal.py) make their own.
    for k, v in {"temporal_url": url, "workflow_engine": "temporal", "temporal_id_prefix": f"cfs{run}", "temporal_task_queue": f"suite-{run}",
                 "temporal_schedules": False,
                 "temporal_sync_seconds": 2.0, "temporal_namespace_mode": os.getenv("TEST_TEMPORAL_NAMESPACES", "shared"),
                 "temporal_namespace_prefix": f"cfs{run}-"}.items():
        object.__setattr__(settings, k, v)
    _SUITE["engine"] = TemporalEngine()
    yield "temporal"
    _SUITE.pop("engine", None)
    for k, v in saved.items():
        object.__setattr__(settings, k, v)


_SUITE: dict = {}


class _Worker:
    """The durable worker in a thread of its own, for as long as one test module drives the application over HTTP.

    Only then: the worker reads whatever database the application is pointed at, and many tests point it at an
    in-memory database whose one connection they are using themselves.
    """

    def __init__(self) -> None:
        import asyncio
        import threading

        from app.durable import worker

        self.loop, self.stop = asyncio.new_event_loop(), None
        ready = threading.Event()

        def serve() -> None:
            asyncio.set_event_loop(self.loop)
            self.stop = asyncio.Event()
            ready.set()
            self.loop.run_until_complete(worker.run(self.stop))

        self.thread = threading.Thread(target=serve, name="durable-worker", daemon=True)
        self.thread.start()
        ready.wait(10)

    def close(self) -> None:
        """Stop the worker and wait until its last activity has returned.

        An activity runs on a thread and cannot be interrupted, and the worker's run waits for it. If this gave up
        early, the activity would carry on after the module, against whatever database the next test points the
        application at, which is often an in-memory one whose single connection that test is using (on a busy CI
        runner a new case's briefing can take longer than 30 seconds).
        """
        if self.stop is not None:
            self.loop.call_soon_threadsafe(self.stop.set)
        self.thread.join(timeout=300)
        if self.thread.is_alive():
            raise RuntimeError("The durable worker did not stop within 5 minutes; an activity is still running.")


@contextmanager
def app_database(engine, factory):
    """Point the application's own sessions (app.db.engine and SessionLocal) at a test's database for a while.

    On the Temporal engine, a module's `client` fixture keeps a durable worker running beside the tests, and its
    activities open sessions through app.db from threads of their own, whenever Temporal hands them work. A test
    that repointed app.db at its in-memory database would share that database's one connection with them, and
    their commits would land in the middle of the test's transactions. So the worker stops first and starts again,
    on the application's database, once the test is done. On the built-in engine there is no worker; nothing stops.
    """
    import app.db as appdb

    worker = _SUITE.pop("worker", None)
    if worker is not None:
        worker.close()
    old = appdb.engine, appdb.SessionLocal
    appdb.engine, appdb.SessionLocal = engine, factory
    try:
        yield
    finally:
        appdb.engine, appdb.SessionLocal = old
        if worker is not None:
            _SUITE["worker"] = _Worker()


def _engine_for_the_app():
    """The application under test runs on the suite's engine: Temporal when asked for, otherwise the built-in one."""
    from app.workflow import use

    if "engine" in _SUITE:
        _SUITE["worker"] = _Worker()
    return use(_SUITE.get("engine"))


def _restore_engine(previous) -> None:
    from app.workflow import use

    use(previous)
    w = _SUITE.pop("worker", None)
    if w is not None:
        w.close()
