"""Q18: provider output that can't be saved must still end the stream cleanly.

The assistant node is committed only after the last token. Before Q18 that
commit was unguarded: if it raised, the SSE body just stopped after the tokens
(no `done`, no `error`), the client showed a generic "stream ended" message,
and the DB session was left mid-transaction. One real way to hit it: a provider
whose JSON stream contains an escaped *unpaired* surrogate (`"\\ud83d"`, half an
emoji). `json.loads` turns that into a lone surrogate in a Python str, which
neither SQLite nor Postgres drivers can encode as UTF-8. Merge notes come from
the same providers and had the same hole (a 500 instead of a note).
"""

from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, select
from sqlalchemy.orm import Session

from app.db.models import Message, Note, Role
from app.db.session import make_session_factory
from app.llm import DEFAULT_MAX_TOKENS, ChatMessage, LLMProvider, MockProvider
from app.main import create_app
from tests.test_merge import build_tree
from tests.test_reply_sse import new_conversation, reply

LONE_HIGH = "\ud83d"
ROCKET = "\U0001f680"  # encoded as the pair 🚀 in JSON


class ChunkProvider(LLMProvider):
    """Streams fixed chunks, whatever the prompt."""

    name = "chunks"

    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks

    async def stream(
        self, messages: list[ChatMessage], max_tokens: int = DEFAULT_MAX_TOKENS
    ) -> AsyncIterator[str]:
        for chunk in self.chunks:
            yield chunk


def client_for(engine: Engine, provider: LLMProvider) -> TestClient:
    return TestClient(create_app(make_session_factory(engine), provider))


def assistants(db: Session) -> list[Message]:
    db.expire_all()
    return list(db.scalars(select(Message).where(Message.role == Role.assistant)))


def test_lone_surrogate_in_reply_is_saved_as_replacement_char(
    engine: Engine, db: Session
) -> None:
    with client_for(engine, ChunkProvider(["rocket ", LONE_HIGH, " launched"])) as c:
        conv = new_conversation(c)
        events = reply(c, conv["root_message_id"], "hi")
    assert [k for k, _ in events] == ["user", "token", "token", "token", "done"]
    assert events[-1][1]["content"] == "rocket � launched"
    [saved] = assistants(db)
    assert saved.content == "rocket � launched"


def test_surrogate_pair_split_across_chunks_is_kept_whole(engine: Engine, db: Session) -> None:
    # Each half alone is a lone surrogate (still unsavable after a plain join); scrubbing
    # the joined text recombines them into one emoji instead of two U+FFFD.
    with client_for(engine, ChunkProvider(["go ", "\ud83d", "\ude80", "!"])) as c:
        conv = new_conversation(c)
        events = reply(c, conv["root_message_id"], "hi")
    assert events[-1] == ("done", events[-1][1])
    assert events[-1][1]["content"] == f"go {ROCKET}!"
    assert assistants(db)[0].content == f"go {ROCKET}!"


def test_whitespace_plus_lone_surrogate_is_not_blank(engine: Engine) -> None:
    # A reply that is only a broken half emoji still has visible content (U+FFFD).
    with client_for(engine, ChunkProvider([" ", LONE_HIGH])) as c:
        conv = new_conversation(c)
        events = reply(c, conv["root_message_id"], "hi")
    assert events[-1][0] == "done"


@pytest.fixture
def failing_save(engine: Engine) -> Iterator[TestClient]:
    """App whose sessions refuse to flush a new assistant message (DB down mid-stream)."""
    factory = make_session_factory(engine)

    @event.listens_for(factory, "before_flush")
    def _refuse_assistants(session: Session, _ctx: object, _instances: object) -> None:
        if any(isinstance(o, Message) and o.role == Role.assistant for o in session.new):
            raise RuntimeError("database went away")

    with TestClient(create_app(factory, MockProvider())) as c:
        yield c


def test_save_failure_emits_error_event_and_keeps_user_turn(
    failing_save: TestClient, db: Session
) -> None:
    conv = new_conversation(failing_save)
    events = reply(failing_save, conv["root_message_id"], "hi")
    kinds = [k for k, _ in events]
    assert kinds[0] == "user" and kinds[-1] == "error"
    assert "done" not in kinds
    assert events[-1][1]["detail"] == "Could not save the reply; please retry"
    assert "database went away" not in events[-1][1]["detail"]  # internals stay in the log
    assert assistants(db) == []
    assert db.get(Message, events[0][1]["id"]) is not None


def test_retry_after_save_failure_reuses_the_user_turn(
    engine: Engine, failing_save: TestClient, db: Session
) -> None:
    conv = new_conversation(failing_save)
    first = reply(failing_save, conv["root_message_id"], "hi")
    assert first[-1][0] == "error"
    with client_for(engine, MockProvider()) as ok:
        second = reply(ok, conv["root_message_id"], "hi")
    assert second[0][1]["id"] == first[0][1]["id"]  # no duplicate sibling
    assert second[-1][0] == "done"
    assert len(assistants(db)) == 1


def test_lone_surrogate_in_merge_summary_is_saved(
    engine: Engine, client: TestClient, db: Session
) -> None:
    ids = build_tree(client)
    with client_for(engine, ChunkProvider(["Heap: named ", LONE_HIGH, "."])) as c:
        r = c.post(f"/api/branches/{ids['b2']}/merge")
    assert r.status_code == 201, r.text
    assert r.json()["content"] == "Heap: named �."
    db.expire_all()
    [note] = db.scalars(select(Note)).all()
    assert note.content == "Heap: named �."
