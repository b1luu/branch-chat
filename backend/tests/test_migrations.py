from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from app.db.base import Base
from app.db.session import make_engine

BACKEND = Path(__file__).resolve().parents[1]


def _config(url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture
def db_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    return f"sqlite:///{tmp_path / 'migrate.db'}"


def test_upgrade_matches_models_and_downgrades(db_url: str) -> None:
    cfg = _config(db_url)
    command.upgrade(cfg, "head")

    engine = make_engine(db_url)
    with engine.connect() as conn:
        assert {"conversations", "messages", "notes"} <= set(inspect(conn).get_table_names())
        # No drift between the migration and the ORM models.
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
        assert diff == []

    command.downgrade(cfg, "base")
    with engine.connect() as conn:
        assert set(inspect(conn).get_table_names()) == {"alembic_version"}
    engine.dispose()
