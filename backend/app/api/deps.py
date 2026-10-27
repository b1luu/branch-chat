"""FastAPI dependencies.

The session factory lives on `app.state` (set by `create_app`) instead of a
module global, so each test can build an app bound to its own database.
"""

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session

from app.llm import LLMProvider


def get_db(request: Request) -> Iterator[Session]:
    """One session per request; commit is explicit in the route that writes."""
    factory = request.app.state.session_factory
    with factory() as session:
        yield session


def get_llm(request: Request) -> LLMProvider:
    """The provider chosen at startup (`LLM_PROVIDER`), overridable in tests."""
    provider: LLMProvider = request.app.state.llm_provider
    return provider
