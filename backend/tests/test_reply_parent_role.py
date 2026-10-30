"""Q8: a reply can't hang under a user turn (it would break user/assistant alternation)."""

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import repository
from app.db.models import Message
from app.services.chat import build_prompt
from tests.test_reply_sse import done, new_conversation, reply


def _count(db: Session) -> int:
    db.expire_all()
    return db.scalar(select(func.count()).select_from(Message)) or 0


def test_reply_to_user_turn_is_409_and_persists_nothing(client: TestClient, db: Session) -> None:
    conv = new_conversation(client)
    events = reply(client, conv["root_message_id"], "first question")
    user_id = events[0][1]["id"]
    before = _count(db)

    r = client.post(f"/api/messages/{user_id}/reply", json={"content": "second question"})

    assert r.status_code == 409
    assert "user turn" in r.json()["detail"]
    assert _count(db) == before


def test_reply_to_unanswered_user_turn_is_409(client: TestClient, db: Session) -> None:
    """A failed reply's orphan is still a user turn: send under its parent to retry."""
    conv = new_conversation(client)
    orphan = Message(
        conversation_id=conv["id"],
        parent_id=conv["root_message_id"],
        role="user",
        content="unanswered",
    )
    db.add(orphan)
    db.commit()

    r = client.post(f"/api/messages/{orphan.id}/reply", json={"content": "hello?"})
    assert r.status_code == 409


def test_asking_in_place_of_a_question_branches_from_its_parent(
    client: TestClient, db: Session
) -> None:
    """The supported way: reply to the question's parent → sibling question, clean alternation."""
    conv = new_conversation(client)
    first = reply(client, conv["root_message_id"], "q1")
    a1 = done(first)
    second = reply(client, a1["id"], "q2")
    q2 = second[0][1]

    alt = done(reply(client, q2["parent_id"], "q2, rephrased"))

    db.expire_all()
    prompt = build_prompt(repository.get_path(db, alt["id"]))
    roles = [m.role for m in prompt]
    assert roles == ["system", "user", "assistant", "user", "assistant"]
    assert "q2" not in [m.content for m in prompt]  # the replaced question isn't in context


def test_reply_to_assistant_and_system_still_allowed(client: TestClient) -> None:
    conv = new_conversation(client)
    a1 = done(reply(client, conv["root_message_id"], "from system root"))
    done(reply(client, a1["id"], "from assistant"))
