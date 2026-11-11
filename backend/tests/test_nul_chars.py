"""Q26: NUL characters (U+0000) never reach the database.

JSON allows `"\\u0000"` (a paste from a binary file or a terminal can carry one)
and a model can emit one. Postgres `text` columns reject NUL outright, so a
reply containing one was a 500 (user input) or a streamed reply that then failed
to save ("Could not save the reply"). On SQLite it was stored, and a stored NUL
collided with `markdown_to_text`'s internal `\\x00N\\x00` placeholders, crashing
`GET /tree` with an IndexError. NUL is invisible and meaningless in chat text,
so it's removed on the way in (requests and provider output).

The endpoint tests run on SQLite and, when TEST_POSTGRES_URL is set, Postgres.
"""

import os
from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.api.validation import clean_text
from app.db.base import Base
from app.db.models import Conversation, Message, Note, Role
from app.db.session import make_engine, make_session_factory
from app.llm import DEFAULT_MAX_TOKENS, ChatMessage, LLMProvider
from app.main import create_app
from app.services.preview import make_preview, markdown_to_text
from tests.test_reply_sse import done, new_conversation, parse_sse, reply


class NulProvider(LLMProvider):
    """Replies (and summarizes) with text that has NULs mixed in."""

    name = "nul"

    async def stream(
        self, messages: list[ChatMessage], max_tokens: int = DEFAULT_MAX_TOKENS
    ) -> AsyncIterator[str]:
        for chunk in ["**bold**\x00 ", "\x00", "`x` and\x00 more"]:
            yield chunk


@pytest.fixture(params=["sqlite", "postgres"])
def nul_engine(request: pytest.FixtureRequest, engine: Engine) -> Iterator[Engine]:
    if request.param == "sqlite":
        yield engine
        return
    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL not set")
    eng = make_engine(url)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def nclient(nul_engine: Engine) -> Iterator[TestClient]:
    with TestClient(create_app(make_session_factory(nul_engine), NulProvider())) as c:
        yield c


def stored(engine: Engine, model: type, **where: str) -> list:
    with Session(engine) as s:
        return list(s.scalars(select(model).filter_by(**where)))


# --- requests -----------------------------------------------------------------


@pytest.mark.parametrize("field", ["content", "quote"])
def test_reply_with_nul_is_saved_without_it(
    nclient: TestClient, nul_engine: Engine, field: str
) -> None:
    parent = done(reply(nclient, new_conversation(nclient)["root_message_id"], "hi"))["id"]
    body = {"content": "what is this?", field: "a\x00b\x00"}
    response = nclient.post(f"/api/messages/{parent}/reply", json=body)
    assert response.status_code == 200, response.text
    events = parse_sse(response.text)
    assert [e for e, _ in events][-1] == "done", events
    user = events[0][1]
    assert user[field] == "ab"
    [row] = stored(nul_engine, Message, id=user["id"])
    assert getattr(row, field) == "ab"


@pytest.mark.parametrize("content", ["\x00", "\x00 \x00\n"])
def test_reply_of_only_nul_is_blank_422(
    nclient: TestClient, nul_engine: Engine, content: str
) -> None:
    root = new_conversation(nclient)["root_message_id"]
    response = nclient.post(f"/api/messages/{root}/reply", json={"content": content})
    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["loc"] == ["body", "content"]
    assert stored(nul_engine, Message, role=Role.user) == []


def test_quote_of_only_nul_means_no_quote(nclient: TestClient) -> None:
    root = new_conversation(nclient)["root_message_id"]
    response = nclient.post(f"/api/messages/{root}/reply", json={"content": "q", "quote": "\x00"})
    assert response.status_code == 200, response.text
    assert parse_sse(response.text)[0][1]["quote"] is None


def test_conversation_title_and_system_prompt_drop_nul(
    nclient: TestClient, nul_engine: Engine
) -> None:
    response = nclient.post(
        "/api/conversations", json={"title": "my\x00 chat", "system_prompt": "be\x00 brief"}
    )
    assert response.status_code == 201, response.text
    assert response.json()["title"] == "my chat"
    [conv] = stored(nul_engine, Conversation)
    assert conv.title == "my chat"
    [root] = stored(nul_engine, Message, role=Role.system)
    assert root.content == "be brief"


@pytest.mark.parametrize("field", ["title", "system_prompt"])
def test_conversation_field_of_only_nul_is_422(
    nclient: TestClient, nul_engine: Engine, field: str
) -> None:
    response = nclient.post("/api/conversations", json={field: "\x00"})
    assert response.status_code == 422, response.text
    with Session(nul_engine) as s:
        assert s.scalar(select(func.count()).select_from(Conversation)) == 0


# --- provider output ------------------------------------------------------------


def test_reply_with_nul_from_the_model_is_saved_without_it(
    nclient: TestClient, nul_engine: Engine
) -> None:
    events = reply(nclient, new_conversation(nclient)["root_message_id"], "hi")
    assistant = done(events)  # Postgres used to fail here: "Could not save the reply"
    assert assistant["content"] == "**bold** `x` and more"
    [row] = stored(nul_engine, Message, id=assistant["id"])
    assert row.content == "**bold** `x` and more"
    tree = nclient.get(f"/api/conversations/{assistant['conversation_id']}/tree")
    assert tree.status_code == 200, tree.text
    previews = {n["id"]: n["preview"] for n in tree.json()["nodes"]}
    assert previews[assistant["id"]] == "bold x and more"


def test_merge_summary_with_nul_is_saved_without_it(
    nclient: TestClient, nul_engine: Engine
) -> None:
    root = new_conversation(nclient)["root_message_id"]
    a1 = done(reply(nclient, root, "main"))["id"]
    done(reply(nclient, a1, "main 2"))
    side = done(reply(nclient, a1, "side question"))["id"]
    response = nclient.post(f"/api/branches/{side}/merge")
    assert response.status_code == 201, response.text
    assert "\x00" not in response.json()["content"]
    [note] = stored(nul_engine, Note)
    assert note.content == "**bold** `x` and more"


# --- previews of rows stored before this fix (SQLite kept the NUL) ----------------


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("plain \x000\x00 text", "plain 0 text"),  # looked like placeholder #0 → IndexError
        ("`code` then \x000\x00", "code then 0"),  # would have pulled in the code span again
        ("a\x00b", "ab"),
    ],
)
def test_markdown_to_text_survives_stored_nul(content: str, expected: str) -> None:
    assert markdown_to_text(content) == expected


def test_tree_preview_of_legacy_nul_row(client: TestClient, db: Session) -> None:
    conv = new_conversation(client)
    db.add(
        Message(
            conversation_id=conv["id"],
            parent_id=conv["root_message_id"],
            role=Role.assistant,
            content="old \x000\x00 reply",
        )
    )
    db.commit()
    response = client.get(f"/api/conversations/{conv['id']}/tree")
    assert response.status_code == 200, response.text
    assert "old 0 reply" in [n["preview"] for n in response.json()["nodes"]]


def test_user_preview_drops_nul() -> None:
    assert make_preview("a\x00b", Role.user, 80) == "ab"


# --- helper ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("ok", "ok"),
        ("a\x00b", "ab"),
        ("\x00", ""),
        ("\ud83d\x00x", "�x"),  # lone surrogate still replaced
        ("\U0001f680", "\U0001f680"),
    ],
)
def test_clean_text(raw: str, clean: str) -> None:
    assert clean_text(raw) == clean


# --- ids in the URL (Q34) -------------------------------------------------------
# `%00` in a path segment decodes to NUL. Postgres rejects NUL in any query
# parameter, so these lookups were 500s there (SQLite found nothing: 404).


def _nul_id_requests(real_id: str) -> list[tuple[str, str, dict]]:
    bad = f"{real_id}%00"
    return [
        ("get", f"/api/messages/{bad}/path", {}),
        ("get", f"/api/conversations/{bad}/tree", {}),
        ("post", f"/api/messages/{bad}/reply", {"json": {"content": "hi"}}),
        ("post", f"/api/branches/{bad}/merge", {}),
    ]


@pytest.mark.parametrize("which", range(4))
def test_id_with_nul_in_the_url_is_404(nclient: TestClient, nul_engine: Engine, which: int) -> None:
    conv = new_conversation(nclient)
    answer = done(reply(nclient, conv["root_message_id"], "hi"))
    # A real id with NUL appended must not match the real row, and the
    # conversation id is used for the tree route.
    real = conv["id"] if which == 1 else answer["id"]
    method, url, kw = _nul_id_requests(real)[which]
    response = getattr(nclient, method)(url, **kw)
    assert response.status_code == 404, response.text
    assert "\x00" not in response.json()["detail"]
    assert len(stored(nul_engine, Message)) == 3  # nothing was added
    # The app still serves normal requests afterwards.
    assert nclient.get(f"/api/messages/{answer['id']}/path").status_code == 200
    assert done(reply(nclient, answer["id"], "and then?"))["role"] == "assistant"


@pytest.mark.parametrize("bad", ["%00", "a%00b", "%00%00"])
def test_message_path_with_only_or_inner_nul_is_404(nclient: TestClient, bad: str) -> None:
    response = nclient.get(f"/api/messages/{bad}/path")
    assert response.status_code == 404, response.text
