import pytest
from sqlalchemy.orm import sessionmaker

from app.cases.case_types import reset_published
from app.db import Base, make_engine


@pytest.fixture
def s():
    reset_published()
    eng = make_engine("sqlite://")
    Base.metadata.create_all(eng)
    with sessionmaker(bind=eng, expire_on_commit=False)() as session:
        yield session
    reset_published()
