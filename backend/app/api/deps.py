"""FastAPI dependencies.

The session factory lives on `app.state` (set by `create_app`) instead of a
module global, so each test can build an app bound to its own database.
"""

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session


def get_db(request: Request) -> Iterator[Session]:
    """One session per request; commit is explicit in the route that writes."""
    factory = request.app.state.session_factory
    with factory() as session:
        yield session
