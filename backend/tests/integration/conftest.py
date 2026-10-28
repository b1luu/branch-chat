"""Fixtures for real-model tests (pytest marker `ollama`).

These run only on a machine with Ollama up (Brandon's PC via
`scripts/local_test.py`). Everywhere else the reachability probe fails fast and
every test here is skipped, so cloud runs stay green without a GPU.
"""

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.db.session import make_session_factory
from app.llm import ChatMessage
from app.llm.ollama import OllamaProvider
from app.main import create_app

PROBE_TIMEOUT_S = 1.0
# Short replies keep each test well under ~60s on a local 8B model.
TEST_MAX_TOKENS = 96
SYSTEM_PROMPT = "You are a test assistant. Answer in one or two short sentences."


class CapturingOllamaProvider(OllamaProvider):
    """Real Ollama provider that records every payload it sends upstream."""

    def __init__(self) -> None:
        super().__init__()
        self.payloads: list[dict[str, Any]] = []

    def build_payload(self, messages: list[ChatMessage], max_tokens: int) -> dict[str, Any]:
        payload = super().build_payload(messages, max_tokens)
        self.payloads.append(payload)
        return payload


def ollama_reachable(base_url: str) -> bool:
    """True if `{base_url}/models` answers 200 with JSON within 1s."""
    try:
        r = httpx.get(f"{base_url}/models", timeout=PROBE_TIMEOUT_S)
        return r.status_code == 200 and isinstance(r.json(), dict)
    except (httpx.HTTPError, json.JSONDecodeError, ValueError):
        return False


@pytest.fixture(scope="session")
def _ollama_available() -> None:
    base_url = OllamaProvider().base_url
    if not ollama_reachable(base_url):
        pytest.skip(f"Ollama not reachable at {base_url}")


@pytest.fixture(autouse=True)
def _require_ollama(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip every `ollama`-marked test when Ollama isn't up; cap reply length."""
    if request.node.get_closest_marker("ollama"):
        request.getfixturevalue("_ollama_available")
        monkeypatch.setenv("LLM_MAX_TOKENS", str(TEST_MAX_TOKENS))


@pytest.fixture
def provider() -> CapturingOllamaProvider:
    return CapturingOllamaProvider()


@pytest.fixture
def live_client(engine: Engine, provider: CapturingOllamaProvider) -> Iterator[TestClient]:
    with TestClient(create_app(make_session_factory(engine), provider)) as c:
        yield c
