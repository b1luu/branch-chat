from collections.abc import Iterator

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.db import models  # noqa: F401  (register tables on Base.metadata)
from app.db.base import Base
from app.db.session import make_engine


@pytest.fixture
def engine() -> Iterator[Engine]:
    """Fresh in-memory SQLite DB per test."""
    eng = make_engine("sqlite://")
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    with Session(engine, expire_on_commit=False) as session:
        yield session
