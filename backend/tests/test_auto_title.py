"""Q33: a conversation still called "New conversation" is named after its first question."""

import json
from collections.abc import AsyncIterator

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import repository
from app.db.models import DEFAULT_TITLE, Conversation
from app.llm import DEFAULT_MAX_TOKENS, ChatMessage, LLMProvider
from app.services.chat import AUTO_TITLE_CHARS


class BrokenProvider(LLMProvider):
    name = "broken"

    async def stream(
        self, messages: list[ChatMessage], max_tokens: int = DEFAULT_MAX_TOKENS
    ) -> AsyncIterator[str]:
        raise RuntimeError("model down")
        yield ""  # unreachable; makes this an async generator


def _create(client: TestClient, **body: str) -> dict:
    response = client.post("/api/conversations", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _reply(client: TestClient, parent_id: str, content: str) -> dict[str, dict]:
    """Send a reply; return its SSE events by name (`user`, `done` or `error`)."""
    with client.stream(
        "POST", f"/api/messages/{parent_id}/reply", json={"content": content}
    ) as r:
        assert r.status_code == 200, r.read()
        text = "".join(r.iter_text())
    events: dict[str, dict] = {}
    for block in text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        events[fields["event"]] = json.loads(fields["data"])
    return events


def _title(client: TestClient, conv_id: str) -> str:
    listed = {c["id"]: c["title"] for c in client.get("/api/conversations").json()}
    return listed[conv_id]


def test_first_question_names_an_untitled_conversation(client: TestClient) -> None:
    conv = _create(client)
    assert "done" in _reply(client, conv["root_message_id"], "How do Rust lifetimes work?")
    assert _title(client, conv["id"]) == "How do Rust lifetimes work?"


def test_title_is_one_line_and_capped(client: TestClient) -> None:
    conv = _create(client)
    question = "Explain   this\ncode:\n\n```\n" + "x = 1\n" * 50 + "```"
    _reply(client, conv["root_message_id"], question)
    title = _title(client, conv["id"])
    assert "\n" not in title and "  " not in title
    assert title.startswith("Explain this code: ``` x = 1")
    assert len(title) == AUTO_TITLE_CHARS and title.endswith("…")


def test_chosen_title_is_kept(client: TestClient) -> None:
    conv = _create(client, title="Rust notes")
    _reply(client, conv["root_message_id"], "How do lifetimes work?")
    assert _title(client, conv["id"]) == "Rust notes"


def test_only_the_first_question_names_it(client: TestClient) -> None:
    conv = _create(client)
    root = conv["root_message_id"]
    events = _reply(client, root, "First question")
    answer_id = events["done"]["id"]
    # A follow-up and a second top-level question (a sibling of the first) don't rename it.
    _reply(client, answer_id, "Follow-up question")
    _reply(client, root, "Another opening question")
    assert _title(client, conv["id"]) == "First question"


def test_follow_up_in_an_untitled_conversation_does_not_name_it(
    client: TestClient, db: Session
) -> None:
    """Only a turn under the root counts as "first": deeper turns never set a title."""
    conv = _create(client)
    events = _reply(client, conv["root_message_id"], "First question")
    # Simulate an untitled conversation that already has turns (e.g. created before Q33).
    row = db.get(Conversation, conv["id"])
    assert row is not None
    row.title = DEFAULT_TITLE
    db.commit()
    _reply(client, events["done"]["id"], "Follow-up question")
    assert _title(client, conv["id"]) == DEFAULT_TITLE


def test_failed_first_reply_still_names_it(client: TestClient) -> None:
    """The title comes from the question, which is saved even if the model fails."""
    client.app.state.llm_provider = BrokenProvider()  # type: ignore[attr-defined]
    conv = _create(client)
    events = _reply(client, conv["root_message_id"], "Why is the sky blue?")
    assert "error" in events and "done" not in events
    assert _title(client, conv["id"]) == "Why is the sky blue?"


def test_title_from_first_turn_never_overwrites(db: Session) -> None:
    conv, _ = repository.create_conversation(db, DEFAULT_TITLE, "sys")
    db.commit()
    repository.title_from_first_turn(db, conv.id, "first")
    repository.title_from_first_turn(db, conv.id, "second")
    db.commit()
    db.refresh(conv)
    assert conv.title == "first"
