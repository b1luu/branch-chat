"""S1: the container's migrate-on-start step (`python -m app.db.migrate`)."""

from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import OperationalError

from app.db import migrate
from app.db.session import make_engine


@pytest.fixture
def db_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    url = f"sqlite:///{tmp_path / 'start.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    return url


def _tables(url: str) -> set[str]:
    engine = make_engine(url)
    with engine.connect() as conn:
        names = set(inspect(conn).get_table_names())
    engine.dispose()
    return names


def test_main_creates_schema_and_is_idempotent(db_url: str) -> None:
    assert migrate.main() == 0
    assert {"conversations", "messages", "notes", "alembic_version"} <= _tables(db_url)
    # Restarting the container runs it again: must be a no-op, not an error.
    assert migrate.main() == 0


def test_wait_for_db_returns_immediately_when_reachable(db_url: str) -> None:
    sleeps: list[float] = []
    migrate.wait_for_db(db_url, attempts=3, sleep=sleeps.append)
    assert sleeps == []


def test_wait_for_db_retries_then_raises(tmp_path: Path) -> None:
    # A SQLite file in a directory that doesn't exist can never be opened.
    url = f"sqlite:///{tmp_path / 'missing' / 'x.db'}"
    sleeps: list[float] = []
    with pytest.raises(OperationalError):
        migrate.wait_for_db(url, attempts=3, delay=0.5, sleep=sleeps.append)
    assert sleeps == [0.5, 0.5]  # slept between attempts, not after the last


def test_wait_for_db_succeeds_once_db_appears(tmp_path: Path) -> None:
    missing = tmp_path / "later"
    url = f"sqlite:///{missing / 'x.db'}"

    def sleep(_delay: float) -> None:
        missing.mkdir(exist_ok=True)  # the "database" comes up during the backoff

    migrate.wait_for_db(url, attempts=3, sleep=sleep)
    assert missing.exists()


def test_main_returns_1_when_db_never_comes_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'nope' / 'x.db'}")
    monkeypatch.setenv("DB_WAIT_ATTEMPTS", "2")
    monkeypatch.setattr(migrate.time, "sleep", lambda _s: None)
    assert migrate.main() == 1
