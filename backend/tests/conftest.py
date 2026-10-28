import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.db import models  # noqa: F401  (register tables on Base.metadata)
from app.db.base import Base
from app.db.session import make_engine, make_session_factory
from app.llm import MockProvider
from app.main import create_app


@pytest.fixture(autouse=True)
def _no_rate_limit_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Most tests fire many replies from one TestClient IP; the limiter (D3) has
    its own tests that enable it explicitly via `create_app(rate_limit=...)`."""
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")


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


@pytest.fixture(params=["sqlite", "postgres"])
def any_db(request: pytest.FixtureRequest, db: Session) -> Iterator[Session]:
    """Runs a test on SQLite and, when TEST_POSTGRES_URL is set, on Postgres too.

    Postgres is optional so cloud/CI runs without a database still pass.
    Example: TEST_POSTGRES_URL=postgresql+psycopg://postgres@127.0.0.1:55432/postgres
    """
    if request.param == "sqlite":
        yield db
        return
    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL not set")
    eng = make_engine(url)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    with Session(eng, expire_on_commit=False) as session:
        yield session
        session.rollback()
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    """API client bound to the per-test in-memory DB (same DB as `db`) and MockProvider."""
    with TestClient(create_app(make_session_factory(engine), MockProvider())) as c:
        yield c
