"""A provider stream that stops early must be an error, not a saved half reply.

When a proxy idle-times-out or the model server restarts, the HTTP body can just
end cleanly: httpx raises nothing, so before Q19 the tokens so far were saved as
if they were the whole reply. Each provider now requires its own end marker
(`[DONE]` / a `finish_reason` for OpenAI-style, `message_stop` for Anthropic).
All HTTP is mocked with respx.
"""

from collections.abc import Iterator

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.db.models import Message, Role
from app.db.session import make_session_factory
from app.llm import ProviderError
from app.llm.anthropic import AnthropicProvider
from app.llm.http_base import StreamState, iter_sse_json
from app.llm.ollama import OllamaProvider
from app.main import create_app
from tests.test_llm_http_providers import (
    ANTHROPIC_URL,
    CONTEXT,
    OLLAMA_URL,
    anthropic_events,
    collect,
    openai_chunk,
    sse_body,
)


def finish_chunk(reason: str = "stop") -> dict:
    return {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]}


# ------------------------------------------------------------- iter_sse_json


async def _events(body: bytes, state: StreamState | None = None) -> list[dict]:
    return [e async for e in iter_sse_json(httpx.Response(200, content=body), state)]


async def test_sse_state_records_done_sentinel() -> None:
    state = StreamState()
    assert await _events(b'data: {"a": 1}\n\ndata: [DONE]\n\n', state) == [{"a": 1}]
    assert state.saw_done is True


async def test_sse_state_stays_false_when_body_just_ends() -> None:
    state = StreamState()
    assert await _events(b'data: {"a": 1}\n\n', state) == [{"a": 1}]
    assert state.saw_done is False


async def test_sse_skips_blank_data_non_objects_and_other_fields() -> None:
    body = (
        b": keep-alive comment\r\n"
        b"event: ping\r\n"
        b"data:\r\n"
        b"data:   \r\n"
        b'data: "just a string"\r\n'
        b"data: [1, 2]\r\n"
        b'data:{"no_space": true}\r\n'
        b"\r\n"
    )
    assert await _events(body) == [{"no_space": True}]


# --------------------------------------------------------------------- Ollama


@respx.mock
async def test_ollama_cut_off_stream_raises_after_partial_tokens() -> None:
    respx.post(OLLAMA_URL).mock(
        return_value=httpx.Response(200, content=sse_body([openai_chunk("half a ")]))
    )
    seen: list[str] = []
    with pytest.raises(ProviderError, match="ended before the reply finished"):
        async for chunk in OllamaProvider().stream(CONTEXT):
            seen.append(chunk)
    assert seen == ["half a "]  # tokens still streamed live; only the end is rejected


@respx.mock
async def test_ollama_empty_200_body_raises() -> None:
    respx.post(OLLAMA_URL).mock(return_value=httpx.Response(200, content=b""))
    with pytest.raises(ProviderError, match="ended before"):
        await collect(OllamaProvider(), CONTEXT)


@respx.mock
async def test_ollama_finish_reason_without_done_is_complete() -> None:
    """Some OpenAI-compatible servers omit `[DONE]`; a finish_reason is enough."""
    respx.post(OLLAMA_URL).mock(
        return_value=httpx.Response(200, content=sse_body([openai_chunk("ok"), finish_chunk()]))
    )
    assert await collect(OllamaProvider(), CONTEXT) == ["ok"]


@respx.mock
async def test_ollama_length_finish_is_complete() -> None:
    """Hitting max_tokens is a normal end (the model was told to stop), not a cut-off."""
    respx.post(OLLAMA_URL).mock(
        return_value=httpx.Response(
            200, content=sse_body([openai_chunk("long"), finish_chunk("length"), "[DONE]"])
        )
    )
    assert await collect(OllamaProvider(), CONTEXT) == ["long"]


# ------------------------------------------------------------------ Anthropic


@respx.mock
async def test_anthropic_stream_without_message_stop_raises() -> None:
    events = anthropic_events("half a ")[:-3]  # drop block stop, message_delta, message_stop
    respx.post(ANTHROPIC_URL).mock(
        return_value=httpx.Response(200, content=sse_body(events, event_names=True))
    )
    with pytest.raises(ProviderError, match="ended before message_stop"):
        await collect(AnthropicProvider(api_key="k"), CONTEXT)


@respx.mock
async def test_anthropic_stream_cut_after_message_delta_raises() -> None:
    events = anthropic_events("almost")[:-1]  # everything but message_stop
    respx.post(ANTHROPIC_URL).mock(
        return_value=httpx.Response(200, content=sse_body(events, event_names=True))
    )
    with pytest.raises(ProviderError, match="message_stop"):
        await collect(AnthropicProvider(api_key="k"), CONTEXT)


# ------------------------------------------------------- through the endpoint


@pytest.fixture
def ollama_client(engine: Engine) -> Iterator[TestClient]:
    with TestClient(create_app(make_session_factory(engine), OllamaProvider())) as c:
        yield c


@respx.mock
def test_cut_off_reply_is_error_and_not_saved_then_retry_reuses_user_turn(
    ollama_client: TestClient, db: Session
) -> None:
    route = respx.post(OLLAMA_URL)
    route.mock(return_value=httpx.Response(200, content=sse_body([openai_chunk("Hal")])))
    root = ollama_client.post("/api/conversations", json={}).json()["root_message_id"]

    body = ollama_client.post(f"/api/messages/{root}/reply", json={"content": "hi"}).text
    assert '"Hal"' in body and "event: error" in body and "event: done" not in body
    assert db.scalars(select(Message).where(Message.role == Role.assistant)).all() == []

    route.mock(
        return_value=httpx.Response(
            200, content=sse_body([openai_chunk("Hello"), finish_chunk(), "[DONE]"])
        )
    )
    body = ollama_client.post(f"/api/messages/{root}/reply", json={"content": "hi"}).text
    assert "event: done" in body
    db.expire_all()
    users = db.scalars(select(Message).where(Message.role == Role.user)).all()
    assert len(users) == 1  # the failed attempt's user turn was reused, no duplicate
    assistant = db.scalars(select(Message).where(Message.role == Role.assistant)).one()
    assert assistant.content == "Hello" and assistant.parent_id == users[0].id
