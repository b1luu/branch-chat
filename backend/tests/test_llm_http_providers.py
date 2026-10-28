"""Ollama and Anthropic providers with HTTP mocked by respx (no real network)."""

import json
from collections.abc import Iterator

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.db.models import Message, Role
from app.db.session import make_session_factory
from app.llm import ChatMessage, LLMProvider, ProviderError
from app.llm.anthropic import (
    ANTHROPIC_VERSION,
    DEFAULT_ANTHROPIC_MODEL,
    AnthropicProvider,
    to_anthropic_messages,
)
from app.llm.ollama import DEFAULT_OLLAMA_BASE_URL, DEFAULT_OLLAMA_MODEL, OllamaProvider
from app.main import create_app

OLLAMA_URL = f"{DEFAULT_OLLAMA_BASE_URL}/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

CONTEXT = [
    ChatMessage("system", "Be brief."),
    ChatMessage("user", "Hi"),
    ChatMessage("assistant", "Hello!"),
    ChatMessage("user", "What is a CTE?"),
]


def sse_body(events: list[dict | str], event_names: bool = False) -> bytes:
    """Build an SSE response body. Strings are sent raw (e.g. "[DONE]")."""
    blocks = []
    for e in events:
        data = e if isinstance(e, str) else json.dumps(e)
        prefix = f"event: {e['type']}\n" if event_names and isinstance(e, dict) else ""
        blocks.append(f"{prefix}data: {data}\n\n")
    return "".join(blocks).encode()


def openai_chunk(text: str | None) -> dict:
    delta = {} if text is None else {"content": text}
    return {"choices": [{"index": 0, "delta": delta}]}


def anthropic_events(*texts: str) -> list[dict | str]:
    events: list[dict | str] = [
        {"type": "message_start", "message": {"id": "msg_1"}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        {"type": "ping"},
    ]
    events += [
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": t}}
        for t in texts
    ]
    events += [
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
        {"type": "message_stop"},
    ]
    return events


async def collect(provider: LLMProvider, messages: list[ChatMessage], **kw: int) -> list[str]:
    return [c async for c in provider.stream(messages, **kw)]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "OLLAMA_BASE_URL",
        "OLLAMA_MODEL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_BASE_URL",
    ):
        monkeypatch.delenv(var, raising=False)


# --------------------------------------------------------------------- Ollama


def test_ollama_defaults() -> None:
    p = OllamaProvider()
    assert p.base_url == "http://localhost:11434/v1"
    assert p.model == DEFAULT_OLLAMA_MODEL == "llama3.1:8b"


def test_ollama_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://gpu-box:11434/v1/")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:7b")
    p = OllamaProvider()
    assert p.base_url == "http://gpu-box:11434/v1"  # trailing slash stripped
    assert p.model == "qwen2.5:7b"


@respx.mock
async def test_ollama_streams_chunks_and_sends_openai_payload() -> None:
    route = respx.post(OLLAMA_URL).mock(
        return_value=httpx.Response(
            200,
            content=sse_body(
                [openai_chunk("A CTE "), openai_chunk(None), openai_chunk("is a query."), "[DONE]"]
            ),
            headers={"content-type": "text/event-stream"},
        )
    )
    chunks = await collect(OllamaProvider(), CONTEXT, max_tokens=50)

    assert chunks == ["A CTE ", "is a query."]  # empty deltas skipped
    sent = json.loads(route.calls.last.request.content)
    assert sent == {
        "model": "llama3.1:8b",
        "messages": [{"role": m.role, "content": m.content} for m in CONTEXT],
        "max_tokens": 50,
        "stream": True,
    }


@respx.mock
async def test_ollama_stops_at_done_sentinel() -> None:
    respx.post(OLLAMA_URL).mock(
        return_value=httpx.Response(
            200, content=sse_body([openai_chunk("one"), "[DONE]", openai_chunk("ignored")])
        )
    )
    assert await collect(OllamaProvider(), CONTEXT) == ["one"]


@respx.mock
async def test_ollama_http_error_raises_provider_error_with_body() -> None:
    respx.post(OLLAMA_URL).mock(
        return_value=httpx.Response(404, json={"error": "model 'llama3.1:8b' not found"})
    )
    with pytest.raises(ProviderError, match=r"HTTP 404.*not found"):
        await collect(OllamaProvider(), CONTEXT)


@respx.mock
async def test_ollama_connection_refused_raises_provider_error() -> None:
    respx.post(OLLAMA_URL).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(ProviderError, match="Could not reach Ollama"):
        await collect(OllamaProvider(), CONTEXT)


@respx.mock
async def test_ollama_in_stream_error_raises() -> None:
    respx.post(OLLAMA_URL).mock(
        return_value=httpx.Response(200, content=sse_body([{"error": {"message": "oom"}}]))
    )
    with pytest.raises(ProviderError, match="oom"):
        await collect(OllamaProvider(), CONTEXT)


@respx.mock
async def test_malformed_stream_event_raises() -> None:
    respx.post(OLLAMA_URL).mock(return_value=httpx.Response(200, content=b"data: {not json\n\n"))
    with pytest.raises(ProviderError, match="Malformed"):
        await collect(OllamaProvider(), CONTEXT)


# ------------------------------------------------------------------ Anthropic


def test_anthropic_requires_key() -> None:
    from app.llm import ProviderConfigError

    with pytest.raises(ProviderConfigError, match="ANTHROPIC_API_KEY"):
        AnthropicProvider()


def test_anthropic_defaults_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert AnthropicProvider().model == DEFAULT_ANTHROPIC_MODEL == "claude-haiku-5-5"
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-x")
    assert AnthropicProvider().model == "claude-sonnet-x"


def test_to_anthropic_messages_splits_system_and_merges_same_role() -> None:
    system, turns = to_anthropic_messages(
        [
            ChatMessage("system", "Be brief."),
            ChatMessage("system", ""),
            ChatMessage("user", "a"),
            ChatMessage("user", "b"),
            ChatMessage("assistant", "c"),
            ChatMessage("system", "Note: x"),
            ChatMessage("user", "d"),
        ]
    )
    assert system == "Be brief.\n\nNote: x"
    assert turns == [
        {"role": "user", "content": "a\n\nb"},
        {"role": "assistant", "content": "c"},
        {"role": "user", "content": "d"},
    ]


def test_anthropic_payload_omits_empty_system() -> None:
    payload = AnthropicProvider(api_key="k").build_payload([ChatMessage("user", "hi")], 10)
    assert "system" not in payload


@respx.mock
async def test_anthropic_streams_text_deltas_with_correct_request() -> None:
    route = respx.post(ANTHROPIC_URL).mock(
        return_value=httpx.Response(
            200, content=sse_body(anthropic_events("A CTE ", "is a query."), event_names=True)
        )
    )
    chunks = await collect(AnthropicProvider(api_key="secret"), CONTEXT, max_tokens=64)

    assert chunks == ["A CTE ", "is a query."]
    req = route.calls.last.request
    assert req.headers["x-api-key"] == "secret"
    assert req.headers["anthropic-version"] == ANTHROPIC_VERSION
    sent = json.loads(req.content)
    assert sent == {
        "model": "claude-haiku-5-5",
        "max_tokens": 64,
        "system": "Be brief.",
        "messages": [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
            {"role": "user", "content": "What is a CTE?"},
        ],
        "stream": True,
    }


@respx.mock
async def test_anthropic_ignores_non_text_deltas() -> None:
    events = anthropic_events("ok")
    events.insert(
        3,
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": "{}"},
        },
    )
    respx.post(ANTHROPIC_URL).mock(return_value=httpx.Response(200, content=sse_body(events)))
    assert await collect(AnthropicProvider(api_key="k"), CONTEXT) == ["ok"]


@respx.mock
async def test_anthropic_http_error_raises() -> None:
    respx.post(ANTHROPIC_URL).mock(
        return_value=httpx.Response(
            401, json={"type": "error", "error": {"message": "invalid x-api-key"}}
        )
    )
    with pytest.raises(ProviderError, match=r"HTTP 401.*invalid x-api-key"):
        await collect(AnthropicProvider(api_key="bad"), CONTEXT)


@respx.mock
async def test_anthropic_in_stream_error_event_raises() -> None:
    respx.post(ANTHROPIC_URL).mock(
        return_value=httpx.Response(
            200,
            content=sse_body(
                [{"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}]
            ),
        )
    )
    with pytest.raises(ProviderError, match="Overloaded"):
        await collect(AnthropicProvider(api_key="k"), CONTEXT)


@respx.mock
async def test_anthropic_timeout_raises_provider_error() -> None:
    respx.post(ANTHROPIC_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ProviderError, match="Could not reach Anthropic"):
        await collect(AnthropicProvider(api_key="k"), CONTEXT)


# ------------------------------------------------------- through the endpoint


@pytest.fixture
def ollama_client(engine: Engine) -> Iterator[TestClient]:
    with TestClient(create_app(make_session_factory(engine), OllamaProvider())) as c:
        yield c


@respx.mock
def test_reply_endpoint_streams_from_mocked_ollama(
    ollama_client: TestClient, db: Session
) -> None:
    respx.post(OLLAMA_URL).mock(
        return_value=httpx.Response(
            200, content=sse_body([openai_chunk("Hel"), openai_chunk("lo"), "[DONE]"])
        )
    )
    root = ollama_client.post("/api/conversations", json={}).json()["root_message_id"]
    body = ollama_client.post(f"/api/messages/{root}/reply", json={"content": "hi"}).text

    assert '"Hel"' in body and '"lo"' in body and "event: done" in body
    assistant = db.scalars(select(Message).where(Message.role == Role.assistant)).one()
    assert assistant.content == "Hello"


@respx.mock
def test_reply_endpoint_reports_ollama_down_without_persisting(
    ollama_client: TestClient, db: Session
) -> None:
    respx.post(OLLAMA_URL).mock(side_effect=httpx.ConnectError("refused"))
    root = ollama_client.post("/api/conversations", json={}).json()["root_message_id"]
    body = ollama_client.post(f"/api/messages/{root}/reply", json={"content": "hi"}).text

    assert "event: error" in body and "Could not reach Ollama" in body
    assert db.scalars(select(Message).where(Message.role == Role.assistant)).all() == []
