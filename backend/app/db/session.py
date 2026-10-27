"""Engine and session factory.

`DATABASE_URL` defaults to a local SQLite file; docker-compose sets a Postgres URL.
SQLite needs `PRAGMA foreign_keys=ON` per connection or it silently ignores FKs.
"""

import os

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

DEFAULT_DATABASE_URL = "sqlite:///./branch_chat.db"


def make_engine(url: str | None = None) -> Engine:
    url = url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    # An in-memory SQLite DB lives only as long as its connection, so share one.
    pool = {"poolclass": StaticPool} if url in ("sqlite://", "sqlite:///:memory:") else {}
    engine = create_engine(url, connect_args=connect_args, **pool)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _enable_sqlite_fks(dbapi_conn, _record) -> None:  # type: ignore[no-untyped-def]
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
