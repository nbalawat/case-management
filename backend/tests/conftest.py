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
    with TestClient(app) as c:
        yield c
    registry.swap(saved)
    appdb.engine, appdb.SessionLocal = old[0], old[1]
    seed_module.engine = old[0]
    object.__setattr__(settings, "samples_dir", old[2])
