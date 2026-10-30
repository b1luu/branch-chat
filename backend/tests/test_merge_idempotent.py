"""Q6: merge back is idempotent per leaf (no duplicate notes from a double-submit)."""

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Note
from app.db.session import make_engine, make_session_factory
from app.llm import DEFAULT_MAX_TOKENS, ChatMessage, LLMProvider, MockProvider
from app.main import create_app
from tests.test_merge import RecordingProvider, build_tree

BACKEND = Path(__file__).resolve().parents[1]


def note_count(engine: Engine, leaf_id: str | None = None) -> int:
    with Session(engine) as s:
        stmt = select(func.count()).select_from(Note)
        if leaf_id is not None:
            stmt = stmt.where(Note.source_leaf_id == leaf_id)
        return s.scalar(stmt) or 0


@pytest.fixture
def recorder() -> RecordingProvider:
    return RecordingProvider()


@pytest.fixture
def rclient(engine: Engine, recorder: RecordingProvider) -> Iterator[TestClient]:
    with TestClient(create_app(make_session_factory(engine), recorder)) as c:
        yield c


def test_merging_same_leaf_twice_returns_existing_note(
    rclient: TestClient, recorder: RecordingProvider, engine: Engine
) -> None:
    ids = build_tree(rclient)
    recorder.calls.clear()

    first = rclient.post(f"/api/branches/{ids['b2']}/merge")
    second = rclient.post(f"/api/branches/{ids['b2']}/merge")

    assert first.status_code == 201
    assert second.status_code == 200
    for key in ("id", "message_id", "source_leaf_id", "content"):
        assert second.json()[key] == first.json()[key]
    assert len(recorder.calls) == 1  # the repeat didn't pay for another summary
    assert note_count(engine, ids["b2"]) == 1


def test_repeat_merge_does_not_duplicate_note_in_context(
    rclient: TestClient, recorder: RecordingProvider
) -> None:
    ids = build_tree(rclient)
    rclient.post(f"/api/branches/{ids['b2']}/merge")
    rclient.post(f"/api/branches/{ids['b2']}/merge")

    path = rclient.get(f"/api/messages/{ids['a2']}/path").json()
    a1 = next(m for m in path if m["id"] == ids["a1"])
    assert len(a1["notes"]) == 1

    recorder.calls.clear()
    r = rclient.post(f"/api/messages/{ids['a2']}/reply", json={"content": "next"})
    assert r.status_code == 200
    [(prompt, _)] = recorder.calls
    notes = [m for m in prompt if m.role == "system" and "merged back" in m.content]
    assert len(notes) == 1


def test_different_leaves_still_get_their_own_notes(
    rclient: TestClient, engine: Engine
) -> None:
    ids = build_tree(rclient)
    assert rclient.post(f"/api/branches/{ids['b1']}/merge").status_code == 201
    assert rclient.post(f"/api/branches/{ids['b2']}/merge").status_code == 201
    assert rclient.post(f"/api/branches/{ids['c1']}/merge").status_code == 201
    assert note_count(engine) == 3


def test_merge_after_failed_merge_creates_note(engine: Engine) -> None:
    """A 502 stores nothing, so the next attempt is a real merge (201)."""

    class FlakyProvider(LLMProvider):
        name = "flaky"
        fail = True

        async def stream(
            self, messages: list[ChatMessage], max_tokens: int = DEFAULT_MAX_TOKENS
        ) -> AsyncIterator[str]:
            if self.fail and messages[0].content.startswith("You condense a side"):
                raise RuntimeError("down")
            async for c in MockProvider().stream(messages, max_tokens):
                yield c

    provider = FlakyProvider()
    with TestClient(create_app(make_session_factory(engine), provider)) as c:
        ids = build_tree(c)
        assert c.post(f"/api/branches/{ids['b2']}/merge").status_code == 502
        provider.fail = False
        assert c.post(f"/api/branches/{ids['b2']}/merge").status_code == 201
    assert note_count(engine, ids["b2"]) == 1


def test_concurrent_merge_of_same_leaf_keeps_winner(engine: Engine) -> None:
    """Simulate two merges racing: while ours is summarizing, the other commits.

    Both passed the "already merged?" check before either stored anything; the
    unique index makes our insert fail, and we return the winner's note.
    """
    state: dict[str, str] = {}

    class RacingProvider(LLMProvider):
        name = "racing"

        async def stream(
            self, messages: list[ChatMessage], max_tokens: int = DEFAULT_MAX_TOKENS
        ) -> AsyncIterator[str]:
            if messages[0].content.startswith("You condense a side") and "leaf" in state:
                with Session(engine) as other:
                    origin = state["origin"]
                    winner = Note(message_id=origin, source_leaf_id=state["leaf"], content="won")
                    other.add(winner)
                    other.commit()
                    state["winner"] = winner.id
                yield "lost"
                return
            async for c in MockProvider().stream(messages, max_tokens):
                yield c

    with TestClient(create_app(make_session_factory(engine), RacingProvider())) as c:
        ids = build_tree(c)
        state.update(leaf=ids["b2"], origin=ids["a1"])
        r = c.post(f"/api/branches/{ids['b2']}/merge")

    assert r.status_code == 200, r.text
    assert r.json()["id"] == state["winner"]
    assert r.json()["content"] == "won"
    assert note_count(engine, ids["b2"]) == 1


def test_db_rejects_second_note_for_same_leaf(client: TestClient, db: Session) -> None:
    ids = build_tree(client)
    db.add(Note(message_id=ids["a1"], source_leaf_id=ids["b2"], content="one"))
    db.commit()
    db.add(Note(message_id=ids["a1"], source_leaf_id=ids["b2"], content="two"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_notes_without_a_leaf_are_not_limited(client: TestClient, db: Session) -> None:
    """source_leaf_id goes NULL when the leaf is deleted; those notes must survive."""
    ids = build_tree(client)
    db.add_all(Note(message_id=ids["a1"], source_leaf_id=None, content=f"n{i}") for i in range(3))
    db.commit()
    assert db.scalar(select(func.count()).select_from(Note)) == 3


# --- migration 0002 -------------------------------------------------------


def _config(url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["configure_logger"] = False
    return cfg


def test_migration_0002_dedupes_existing_notes_keeping_oldest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    url = f"sqlite:///{tmp_path / 'dedupe.db'}"
    cfg = _config(url)
    command.upgrade(cfg, "0001")

    t0 = datetime(2026, 10, 1, tzinfo=UTC)
    engine = make_engine(url)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO conversations VALUES ('c', 't', :t)"), {"t": t0.isoformat()})
        for mid, parent in (("root", None), ("a1", "root"), ("leaf", "a1"), ("leaf2", "a1")):
            conn.execute(
                text("INSERT INTO messages VALUES (:id, 'c', :p, 'user', 'x', NULL, :t)"),
                {"id": mid, "p": parent, "t": t0.isoformat()},
            )
        rows = [
            ("n-new", "leaf", t0 + timedelta(minutes=5)),
            ("n-old", "leaf", t0 + timedelta(minutes=1)),
            ("n-mid", "leaf", t0 + timedelta(minutes=3)),
            ("n-other", "leaf2", t0 + timedelta(minutes=9)),
            ("n-null1", None, t0),
            ("n-null2", None, t0),
        ]
        for nid, leaf, t in rows:
            conn.execute(
                text("INSERT INTO notes VALUES (:id, 'a1', :leaf, 'c', :t)"),
                {"id": nid, "leaf": leaf, "t": t.isoformat()},
            )

    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        kept = set(conn.scalars(text("SELECT id FROM notes")).all())
        indexes = {ix["name"]: ix for ix in inspect(conn).get_indexes("notes")}
    assert kept == {"n-old", "n-other", "n-null1", "n-null2"}
    assert indexes["uq_notes_source_leaf_id"]["unique"]

    command.downgrade(cfg, "0001")
    with engine.connect() as conn:
        assert "uq_notes_source_leaf_id" not in {
            ix["name"] for ix in inspect(conn).get_indexes("notes")
        }
    engine.dispose()
