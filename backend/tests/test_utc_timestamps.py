"""Q7: timestamps are UTC-aware whether a row is fresh or read back from the DB.

SQLite has no time zone type and returns naive datetimes; the API then emitted
`created_at` without a `Z`, so the same row had two different timestamps
depending on whether it was just created or loaded, and browsers parse a naive
ISO string as *local* time.
"""

from datetime import UTC, datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import repository
from app.db.models import Conversation, Message, Note, Role
from tests.test_merge import build_tree, reply


def is_utc_iso(value: str) -> bool:
    return value.endswith(("Z", "+00:00"))


def test_api_timestamps_read_from_db_are_utc(client: TestClient) -> None:
    ids = build_tree(client)
    client.post(f"/api/branches/{ids['b2']}/merge")
    [conv] = client.get("/api/conversations").json()
    tree = client.get(f"/api/conversations/{conv['id']}/tree").json()
    path = client.get(f"/api/messages/{ids['a2']}/path").json()

    stamps = [conv["created_at"]]
    stamps += [n["created_at"] for n in tree["nodes"]]
    stamps += [m["created_at"] for m in path]
    stamps += [n["created_at"] for m in path for n in m["notes"]]
    assert len(stamps) > 10
    assert all(is_utc_iso(s) for s in stamps), stamps


def test_repeat_merge_returns_identical_note_json(client: TestClient) -> None:
    """Fresh (in-memory) and reloaded (from DB) notes serialize the same."""
    ids = build_tree(client)
    first = client.post(f"/api/branches/{ids['b2']}/merge")
    second = client.post(f"/api/branches/{ids['b2']}/merge")
    assert (first.status_code, second.status_code) == (201, 200)
    assert second.json() == first.json()


def test_reply_done_event_matches_reloaded_path(client: TestClient) -> None:
    ids = build_tree(client)
    fresh = reply(client, ids["b2"], "more")  # from the `done` event
    reloaded = client.get(f"/api/messages/{fresh['id']}/path").json()[-1]
    assert is_utc_iso(fresh["created_at"])
    assert reloaded["created_at"] == fresh["created_at"]


def _conversation(db: Session) -> Message:
    _conv, root = repository.create_conversation(db, "t", "sys")
    db.commit()
    return root


def test_round_trip_is_utc_aware(any_db: Session) -> None:
    root = _conversation(any_db)
    plus_two = timezone(timedelta(hours=2))
    stamp = datetime(2026, 10, 30, 12, 0, tzinfo=plus_two)
    note = Note(message_id=root.id, source_leaf_id=None, content="n", created_at=stamp)
    any_db.add(note)
    any_db.commit()
    any_db.expire_all()

    loaded = any_db.scalars(select(Note).where(Note.id == note.id)).one()
    assert loaded.created_at.tzinfo is not None
    assert loaded.created_at.utcoffset() == timedelta(0)
    assert loaded.created_at == stamp  # same instant
    assert loaded.created_at.hour == 10

    conv = any_db.get(Conversation, root.conversation_id)
    assert conv is not None and conv.created_at.utcoffset() == timedelta(0)


def test_order_follows_instant_not_wall_clock(any_db: Session) -> None:
    """A non-UTC aware value is stored as UTC, so ordering stays by real time.

    `late` has the smaller wall-clock reading (09:00 at -05:00 = 14:00 UTC) but
    is later than `early` (12:00 UTC). Before Q7, SQLite stored the wall-clock
    text and sorted `late` first.
    """
    root = _conversation(any_db)
    minus_five = timezone(timedelta(hours=-5))
    early = Message(
        conversation_id=root.conversation_id,
        parent_id=root.id,
        role=Role.user,
        content="early",
        created_at=datetime(2026, 10, 30, 12, 0, tzinfo=UTC),
    )
    late = Message(
        conversation_id=root.conversation_id,
        parent_id=root.id,
        role=Role.user,
        content="late",
        created_at=datetime(2026, 10, 30, 9, 0, tzinfo=minus_five),
    )
    any_db.add_all([late, early])
    any_db.commit()
    any_db.expire_all()

    tree = repository.get_tree(any_db, root.conversation_id)
    assert [m.content for m in tree if m.parent_id == root.id] == ["early", "late"]
