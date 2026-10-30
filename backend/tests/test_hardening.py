"""S4: input validation and retry-after-failure behavior of the API."""

from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.db.models import Message, Role
from app.db.session import make_session_factory
from app.llm import DEFAULT_MAX_TOKENS, ChatMessage, LLMProvider, MockProvider
from app.main import create_app
from tests.test_reply_sse import new_conversation, reply


class FlakyProvider(LLMProvider):
    """Fails the first `failures` calls mid-stream, then behaves like MockProvider."""

    name = "flaky"

    def __init__(self, failures: int = 1) -> None:
        self.failures = failures

    async def stream(
        self, messages: list[ChatMessage], max_tokens: int = DEFAULT_MAX_TOKENS
    ) -> AsyncIterator[str]:
        if self.failures > 0:
            self.failures -= 1
            yield "partial "
            raise RuntimeError("model crashed")
        async for chunk in MockProvider().stream(messages, max_tokens):
            yield chunk


def user_turns(db: Session) -> list[Message]:
    db.expire_all()
    return list(db.scalars(select(Message).where(Message.role == Role.user)).all())


# --- validation ----------------------------------------------------------------


@pytest.mark.parametrize("content", [" ", "   \n\t  "])
def test_blank_reply_content_rejected_and_nothing_persisted(
    client: TestClient, db: Session, content: str
) -> None:
    conv = new_conversation(client)
    response = client.post(
        f"/api/messages/{conv['root_message_id']}/reply", json={"content": content}
    )
    assert response.status_code == 422
    assert "must not be blank" in response.text
    assert user_turns(db) == []


def test_reply_content_whitespace_is_kept_verbatim(client: TestClient, db: Session) -> None:
    conv = new_conversation(client)
    events = reply(client, conv["root_message_id"], "  indented\n    code\n")
    assert events[0][1]["content"] == "  indented\n    code\n"


def test_reply_content_over_limit_rejected(client: TestClient) -> None:
    conv = new_conversation(client)
    response = client.post(
        f"/api/messages/{conv['root_message_id']}/reply", json={"content": "x" * 50_001}
    )
    assert response.status_code == 422


@pytest.mark.parametrize("quote", ["", "   "])
def test_blank_quote_is_treated_as_no_quote(client: TestClient, quote: str) -> None:
    conv = new_conversation(client)
    events = reply(client, conv["root_message_id"], "hi", quote=quote)
    assert events[0][1]["quote"] is None


def test_quote_over_limit_rejected(client: TestClient) -> None:
    conv = new_conversation(client)
    response = client.post(
        f"/api/messages/{conv['root_message_id']}/reply",
        json={"content": "hi", "quote": "x" * 20_001},
    )
    assert response.status_code == 422


def test_missing_content_rejected(client: TestClient) -> None:
    conv = new_conversation(client)
    response = client.post(f"/api/messages/{conv['root_message_id']}/reply", json={})
    assert response.status_code == 422


@pytest.mark.parametrize("field", ["title", "system_prompt"])
def test_blank_conversation_fields_rejected(client: TestClient, field: str) -> None:
    response = client.post("/api/conversations", json={field: "   "})
    assert response.status_code == 422
    assert "must not be blank" in response.text


def test_conversation_title_is_trimmed_and_single_line(client: TestClient) -> None:
    response = client.post("/api/conversations", json={"title": "  Rust \n lifetimes  "})
    assert response.status_code == 201
    assert response.json()["title"] == "Rust lifetimes"


def test_conversation_title_over_limit_rejected(client: TestClient) -> None:
    response = client.post("/api/conversations", json={"title": "x" * 201})
    assert response.status_code == 422


def test_unknown_conversation_tree_404(client: TestClient) -> None:
    assert client.get("/api/conversations/nope/tree").status_code == 404


def test_unknown_message_path_404(client: TestClient) -> None:
    assert client.get("/api/messages/nope/path").status_code == 404


# --- retry after a failed reply ---------------------------------------------------


def test_retry_after_provider_error_reuses_the_orphan_user_turn(
    engine: Engine, db: Session
) -> None:
    with TestClient(create_app(make_session_factory(engine), FlakyProvider(failures=1))) as c:
        conv = new_conversation(c)
        root = conv["root_message_id"]
        failed = reply(c, root, "hi")
        assert failed[-1][0] == "error"
        retried = reply(c, root, "hi")
    assert retried[-1][0] == "done"
    # Same user node, now answered: no spurious sibling branch.
    assert retried[0][1]["id"] == failed[0][1]["id"]
    assert retried[-1][1]["parent_id"] == failed[0][1]["id"]
    assert [m.id for m in user_turns(db)] == [failed[0][1]["id"]]


def test_retry_with_different_text_creates_a_new_turn(engine: Engine, db: Session) -> None:
    with TestClient(create_app(make_session_factory(engine), FlakyProvider(failures=1))) as c:
        conv = new_conversation(c)
        failed = reply(c, conv["root_message_id"], "hi")
        retried = reply(c, conv["root_message_id"], "hi there")
    assert retried[0][1]["id"] != failed[0][1]["id"]
    assert len(user_turns(db)) == 2


def test_retry_with_a_different_quote_creates_a_new_turn(engine: Engine, db: Session) -> None:
    with TestClient(create_app(make_session_factory(engine), FlakyProvider(failures=1))) as c:
        conv = new_conversation(c)
        failed = reply(c, conv["root_message_id"], "why?", quote="a")
        retried = reply(c, conv["root_message_id"], "why?", quote="b")
        unquoted = reply(c, conv["root_message_id"], "why?")
    ids = {failed[0][1]["id"], retried[0][1]["id"], unquoted[0][1]["id"]}
    assert len(ids) == 3


def test_same_question_after_a_successful_reply_is_a_new_branch(
    client: TestClient, db: Session
) -> None:
    conv = new_conversation(client)
    first = reply(client, conv["root_message_id"], "hi")
    second = reply(client, conv["root_message_id"], "hi")
    # The first turn was answered, so asking again is a deliberate sibling branch.
    assert second[0][1]["id"] != first[0][1]["id"]
    assert len(user_turns(db)) == 2


def test_orphan_is_only_reused_under_the_same_parent(engine: Engine, db: Session) -> None:
    with TestClient(create_app(make_session_factory(engine), FlakyProvider(failures=1))) as c:
        conv = new_conversation(c)
        failed = reply(c, conv["root_message_id"], "hi")
        other = new_conversation(c)
        elsewhere = reply(c, other["root_message_id"], "hi")
    assert elsewhere[0][1]["id"] != failed[0][1]["id"]


# --- empty model output (Q5) ---------------------------------------------------


class EmptyThenMockProvider(LLMProvider):
    """Returns nothing (or only whitespace) for the first `empties` calls, then MockProvider."""

    name = "empty"

    def __init__(self, empties: int = 1, chunks: tuple[str, ...] = ()) -> None:
        self.empties = empties
        self.chunks = chunks

    async def stream(
        self, messages: list[ChatMessage], max_tokens: int = DEFAULT_MAX_TOKENS
    ) -> AsyncIterator[str]:
        if self.empties > 0:
            self.empties -= 1
            for c in self.chunks:
                yield c
            return
        async for chunk in MockProvider().stream(messages, max_tokens):
            yield chunk


def assistant_turns(db: Session) -> list[Message]:
    db.expire_all()
    return list(db.scalars(select(Message).where(Message.role == Role.assistant)).all())


@pytest.mark.parametrize("chunks", [(), ("",), (" ", "\n\n", "\t")])
def test_empty_model_reply_is_an_error_and_not_persisted(
    engine: Engine, db: Session, chunks: tuple[str, ...]
) -> None:
    """A blank assistant node would be a dead bubble in the UI and makes Anthropic
    reject every later turn on that branch (it requires non-empty content)."""
    provider = EmptyThenMockProvider(empties=1, chunks=chunks)
    with TestClient(create_app(make_session_factory(engine), provider)) as c:
        conv = new_conversation(c)
        events = reply(c, conv["root_message_id"], "hi")
    kinds = [k for k, _ in events]
    assert kinds[0] == "user" and kinds[-1] == "error"
    assert "done" not in kinds
    assert "empty" in events[-1][1]["detail"]
    assert assistant_turns(db) == []
    assert db.get(Message, events[0][1]["id"]) is not None  # kept for retry


def test_retry_after_empty_reply_reuses_the_user_turn(engine: Engine, db: Session) -> None:
    with TestClient(create_app(make_session_factory(engine), EmptyThenMockProvider())) as c:
        conv = new_conversation(c)
        first = reply(c, conv["root_message_id"], "hi")
        second = reply(c, conv["root_message_id"], "hi")
    assert second[-1][0] == "done"
    assert second[0][1]["id"] == first[0][1]["id"]
    assert len(user_turns(db)) == 1
    [answer] = assistant_turns(db)
    assert answer.content.strip()
