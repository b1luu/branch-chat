"""Q13: request strings with unpaired UTF-16 surrogates get a 422, not a 500.

JSON lets a client send `"\\ud83d"` (half of an emoji's surrogate pair).
Pydantic rejects it, but FastAPI's default 422 echoes the `input` back and
crashed encoding it as UTF-8, so the client saw a 500. The frontend could
produce one by cutting a quote mid-emoji. Bodies are sent as raw bytes here
because `json=` refuses to encode a lone surrogate on the client side.
"""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.validation import scrub_surrogates
from app.db.models import Conversation, Message, Role
from tests.test_reply_sse import done, new_conversation, parse_sse, reply

LONE_HIGH = "\ud83d"  # high surrogate with no low half
LONE_LOW = "\ude80"  # low surrogate with no high half


def post_raw(client: TestClient, url: str, body: dict) -> object:
    # ensure_ascii=True writes surrogates as \uXXXX escapes, like a browser's JSON.stringify.
    return client.post(
        url,
        content=json.dumps(body, ensure_ascii=True).encode("ascii"),
        headers={"Content-Type": "application/json"},
    )


def assistant_id(client: TestClient) -> tuple[str, dict]:
    conv = new_conversation(client)
    return done(reply(client, conv["root_message_id"], "hi"))["id"], conv


def user_count(db: Session) -> int:
    db.expire_all()
    return db.scalar(select(func.count()).select_from(Message).where(Message.role == Role.user))


@pytest.mark.parametrize("bad", [f"rocket {LONE_HIGH}", f"{LONE_LOW} tail", LONE_HIGH])
@pytest.mark.parametrize("field", ["content", "quote"])
def test_reply_with_unpaired_surrogate_is_422_and_nothing_persisted(
    client: TestClient, db: Session, field: str, bad: str
) -> None:
    parent, _ = assistant_id(client)
    before = user_count(db)
    body = {"content": "what is this?", field: bad}
    response = post_raw(client, f"/api/messages/{parent}/reply", body)
    assert response.status_code == 422, response.text
    [error] = response.json()["detail"]
    assert error["type"] == "string_unicode"
    assert error["loc"] == ["body", field]
    assert "\ufffd" in error["input"]  # echoed with the lone half replaced
    assert user_count(db) == before


@pytest.mark.parametrize("field", ["title", "system_prompt"])
def test_conversation_with_unpaired_surrogate_is_422(
    client: TestClient, db: Session, field: str
) -> None:
    response = post_raw(client, "/api/conversations", {field: f"x{LONE_HIGH}"})
    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["input"] == "x\ufffd"
    assert db.scalar(select(func.count()).select_from(Conversation)) == 0


def test_escaped_surrogate_pair_is_a_valid_emoji(client: TestClient) -> None:
    # A *complete* pair sent as escapes decodes to one code point and is fine.
    parent, _ = assistant_id(client)
    response = post_raw(
        client,
        f"/api/messages/{parent}/reply",
        {"content": "launch \U0001f680?", "quote": "\U0001f680 rocket"},
    )
    assert response.status_code == 200, response.text
    user = next(e for e in parse_sse(response.text) if e[0] == "user")[1]
    assert user["content"] == "launch \U0001f680?"
    assert user["quote"] == "\U0001f680 rocket"


def test_length_limits_count_code_points(client: TestClient) -> None:
    # The frontend counts code points to match this: 50,000 emoji is allowed.
    parent, _ = assistant_id(client)
    response = post_raw(client, f"/api/messages/{parent}/reply", {"content": "\U0001f680" * 50_000})
    assert response.status_code == 200, response.text
    over = post_raw(client, f"/api/messages/{parent}/reply", {"content": "\U0001f680" * 50_001})
    assert over.status_code == 422


def test_ordinary_422_shape_is_unchanged(client: TestClient) -> None:
    # The custom handler only scrubs; other validation errors look like FastAPI's.
    conv = new_conversation(client)
    response = client.post(f"/api/messages/{conv['root_message_id']}/reply", json={})
    assert response.status_code == 422
    [error] = response.json()["detail"]
    assert error["type"] == "missing"
    assert error["loc"] == ["body", "content"]


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("ok", "ok"),
        ("\U0001f680", "\U0001f680"),  # a real pair is kept
        (f"a{LONE_HIGH}b", "a\ufffdb"),
        (f"{LONE_LOW}", "\ufffd"),
        ({"k": [f"{LONE_HIGH}", 1, None]}, {"k": ["\ufffd", 1, None]}),
    ],
)
def test_scrub_surrogates(raw: object, clean: object) -> None:
    assert scrub_surrogates(raw) == clean
