"""Migrations must close their DB connections instead of leaving them to GC.

Before the fix, `alembic/env.py` never disposed its engine, so every
`alembic upgrade` (the container entrypoint, and the migration tests) left a
pooled SQLite connection open until garbage collection — showing up as
`ResourceWarning: unclosed database` in the test run.
"""

import gc
import warnings
from pathlib import Path

import pytest

from app.db import migrate


def test_migrate_main_leaves_no_open_connections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'leak.db'}")
    gc.collect()  # flush anything earlier tests left behind
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        assert migrate.main() == 0
        assert migrate.main() == 0  # idempotent rerun opens/closes again
        gc.collect()
    leaks = [w for w in caught if issubclass(w.category, ResourceWarning)]
    assert leaks == [], [str(w.message) for w in leaks]
