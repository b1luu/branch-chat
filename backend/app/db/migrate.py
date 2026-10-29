"""Run Alembic migrations on container start, waiting for the database first.

`python -m app.db.migrate` is the API container's first step (see
`backend/docker-entrypoint.sh`). Compose already waits for Postgres's
healthcheck, but a fresh Postgres can still refuse connections for a moment
after it reports healthy (or restart during init), so we retry the connection
with a short backoff before running `alembic upgrade head`. Upgrading is
idempotent: an already-current schema is a no-op, so restarts are safe.
"""

import logging
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.db.session import DEFAULT_DATABASE_URL, make_engine

logger = logging.getLogger("app.db.migrate")

BACKEND_DIR = Path(__file__).resolve().parents[2]


def database_url() -> str:
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


def wait_for_db(
    url: str,
    attempts: int = 30,
    delay: float = 1.0,
    sleep: Callable[[float], None] | None = None,
) -> None:
    """Block until `SELECT 1` succeeds on `url`; raise the last error after `attempts` tries."""
    pause = sleep or time.sleep
    engine = make_engine(url)
    try:
        for attempt in range(1, attempts + 1):
            try:
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                return
            except OperationalError:
                if attempt == attempts:
                    raise
                logger.warning("database not ready (attempt %d/%d), retrying", attempt, attempts)
                pause(delay)
    finally:
        engine.dispose()


def alembic_config(url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    # main() already configured logging; alembic.ini's fileConfig would disable existing loggers.
    cfg.attributes["configure_logger"] = False
    return cfg


def upgrade(url: str) -> None:
    # env.py prefers DATABASE_URL over the ini, so pin it to the URL we waited on.
    os.environ["DATABASE_URL"] = url
    command.upgrade(alembic_config(url), "head")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    url = database_url()
    attempts = int(os.getenv("DB_WAIT_ATTEMPTS", "30"))
    try:
        wait_for_db(url, attempts=attempts)
    except OperationalError as exc:
        logger.error("database unreachable after %d attempts: %s", attempts, exc)
        return 1
    upgrade(url)
    logger.info("migrations at head")
    return 0


if __name__ == "__main__":
    sys.exit(main())
