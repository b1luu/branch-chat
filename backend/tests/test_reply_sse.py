import json
from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.db.models import Message, Role
from app.db.session import make_session_factory
from app.llm import DEFAULT_MAX_TOKENS, ChatMessage, LLMProvider, MockProvider
from app.llm.mock import mock_reply
from app.main import create_app
from app.services.chat import build_prompt, format_user_content


class RecordingProvider(LLMProvider):
    """Wraps MockProvider and records every prompt it is sent."""

    name = "recording"

    def __init__(self) -> None:
        self.calls: list[tuple[list[ChatMessage], int]] = []

    async def stream(
        self, messages: list[ChatMessage], max_tokens: int = DEFAULT_MAX_TOKENS
    ) -> AsyncIterator[str]:
        self.calls.append((messages, max_tokens))
        async for chunk in MockProvider().stream(messages, max_tokens):
            yield chunk


class FailingProvider(LLMProvider):
    name = "failing"

    async def stream(
        self, messages: list[ChatMessage], max_tokens: int = DEFAULT_MAX_TOKENS
    ) -> AsyncIterator[str]:
        yield "partial "
        raise RuntimeError("model crashed")


@pytest.fixture
def recorder() -> RecordingProvider:
    return RecordingProvider()


@pytest.fixture
def rclient(engine: Engine, recorder: RecordingProvider) -> Iterator[TestClient]:
    with TestClient(create_app(make_session_factory(engine), recorder)) as c:
        yield c


def parse_sse(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def new_conversation(client: TestClient) -> dict:
    return client.post("/api/conversations", json={}).json()


def reply(client: TestClient, parent_id: str, content: str, quote: str | None = None):
    body: dict = {"content": content}
    if quote is not None:
        body["quote"] = quote
    response = client.post(f"/api/messages/{parent_id}/reply", json=body)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return parse_sse(response.text)


def done(events: list[tuple[str, dict]]) -> dict:
    assert events[-1][0] == "done"
    return events[-1][1]


# --- stream shape ------------------------------------------------------------


def test_stream_emits_user_tokens_done(client: TestClient) -> None:
    conv = new_conversation(client)
    events = reply(client, conv["root_message_id"], "hello there")
    kinds = [k for k, _ in events]
    assert kinds[0] == "user"
    assert kinds[-1] == "done"
    assert kinds[1:-1] and set(kinds[1:-1]) == {"token"}
    text = "".join(d["text"] for k, d in events if k == "token")
    assert text == 'Mock reply to "hello there" with 2 context messages.'
    assert done(events)["content"] == text


def test_both_nodes_persisted_with_correct_parents(client: TestClient, db: Session) -> None:
    conv = new_conversation(client)
    events = reply(client, conv["root_message_id"], "hi")
    user_id = events[0][1]["id"]
    assistant_id = done(events)["id"]

    user = db.get(Message, user_id)
    assistant = db.get(Message, assistant_id)
    assert user is not None and assistant is not None
    assert (user.parent_id, user.role, user.content) == (conv["root_message_id"], "user", "hi")
    assert (assistant.parent_id, assistant.role) == (user.id, "assistant")
    assert assistant.conversation_id == user.conversation_id == conv["id"]
    assert assistant.content == done(events)["content"]


def test_multi_turn_context_grows(client: TestClient) -> None:
    conv = new_conversation(client)
    first = done(reply(client, conv["root_message_id"], "one"))
    second = done(reply(client, first["id"], "two"))
    # system, user one, assistant, user two
    assert second["content"] == 'Mock reply to "two" with 4 context messages.'


# --- context / branching -----------------------------------------------------


def test_context_is_exactly_the_path(rclient: TestClient, recorder: RecordingProvider) -> None:
    conv = new_conversation(rclient)
    a1 = done(reply(rclient, conv["root_message_id"], "q1"))
    reply(rclient, a1["id"], "q2")
    prompt, _ = recorder.calls[-1]
    assert [(m.role, m.content) for m in prompt] == [
        ("system", "You are a helpful assistant."),
        ("user", "q1"),
        ("assistant", a1["content"]),
        ("user", "q2"),
    ]


def test_branching_from_non_leaf_isolates_siblings(
    rclient: TestClient, recorder: RecordingProvider, db: Session
) -> None:
    conv = new_conversation(rclient)
    a1 = done(reply(rclient, conv["root_message_id"], "main question"))
    a2 = done(reply(rclient, a1["id"], "main follow-up"))
    # a1 now has a child; replying to it again creates a side branch.
    side = reply(rclient, a1["id"], "side question")

    prompt, _ = recorder.calls[-1]
    contents = [m.content for m in prompt]
    assert contents[-1] == "side question"
    assert "main follow-up" not in contents
    assert a2["content"] not in contents
    assert len(prompt) == 4

    children = db.scalars(select(Message).where(Message.parent_id == a1["id"])).all()
    assert {c.content for c in children} == {"main follow-up", "side question"}
    assert side[0][1]["parent_id"] == a1["id"]


def test_quote_is_stored_and_included_in_prompt(
    rclient: TestClient, recorder: RecordingProvider, db: Session
) -> None:
    conv = new_conversation(rclient)
    a1 = done(reply(rclient, conv["root_message_id"], "explain rust"))
    events = reply(rclient, a1["id"], "what does this mean?", quote="borrow checker")
    assert events[0][1]["quote"] == "borrow checker"
    assert db.get(Message, events[0][1]["id"]).quote == "borrow checker"  # type: ignore[union-attr]

    last = recorder.calls[-1][0][-1]
    assert last.role == "user"
    assert "borrow checker" in last.content
    assert "what does this mean?" in last.content


def test_max_tokens_from_env_is_passed(
    rclient: TestClient, recorder: RecordingProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_MAX_TOKENS", "3")
    conv = new_conversation(rclient)
    events = reply(rclient, conv["root_message_id"], "hi")
    assert recorder.calls[-1][1] == 3
    assert sum(1 for k, _ in events if k == "token") == 3


# --- errors ------------------------------------------------------------------


def test_missing_parent_404_and_nothing_persisted(client: TestClient, db: Session) -> None:
    response = client.post("/api/messages/nope/reply", json={"content": "hi"})
    assert response.status_code == 404
    assert db.scalars(select(Message).where(Message.role == Role.user)).all() == []


def test_empty_content_rejected(client: TestClient) -> None:
    conv = new_conversation(client)
    response = client.post(f"/api/messages/{conv['root_message_id']}/reply", json={"content": ""})
    assert response.status_code == 422


def test_provider_error_emits_error_event_and_no_assistant(engine: Engine, db: Session) -> None:
    with TestClient(create_app(make_session_factory(engine), FailingProvider())) as c:
        conv = new_conversation(c)
        events = reply(c, conv["root_message_id"], "hi")
    assert [k for k, _ in events] == ["user", "token", "error"]
    assert "model crashed" in events[-1][1]["detail"]
    assert db.scalars(select(Message).where(Message.role == Role.assistant)).all() == []
    # The user's turn is kept so they can retry from it.
    assert db.get(Message, events[0][1]["id"]) is not None


# --- prompt building (unit) --------------------------------------------------


def test_format_user_content_without_quote_is_unchanged() -> None:
    assert format_user_content("hi", None) == "hi"
    assert format_user_content("hi", "") == "hi"


def test_build_prompt_maps_roles_and_quotes() -> None:
    path = [
        Message(role=Role.system, content="sys"),
        Message(role=Role.user, content="q", quote=None),
        Message(role=Role.assistant, content="a"),
        Message(role=Role.user, content="why?", quote="x"),
    ]
    prompt = build_prompt(path)
    assert [m.role for m in prompt] == ["system", "user", "assistant", "user"]
    assert prompt[3].content == format_user_content("why?", "x")
    assert mock_reply(prompt).endswith("with 4 context messages.")
