import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Conversation, Message, Note, Role


def _conversation(db: Session) -> Conversation:
    conv = Conversation(title="t")
    db.add(conv)
    db.flush()
    return conv


def test_create_conversation_with_message_tree(db: Session) -> None:
    conv = _conversation(db)
    root = Message(conversation_id=conv.id, role=Role.user, content="hi")
    db.add(root)
    db.flush()
    a = Message(conversation_id=conv.id, parent_id=root.id, role=Role.assistant, content="a")
    b = Message(conversation_id=conv.id, parent_id=root.id, role=Role.assistant, content="b")
    db.add_all([a, b])
    db.commit()
    db.expire_all()

    root = db.get(Message, root.id)
    assert root is not None
    assert root.parent is None
    assert {c.content for c in root.children} == {"a", "b"}
    assert db.get(Message, a.id).parent.id == root.id  # type: ignore[union-attr]
    assert len(db.get(Conversation, conv.id).messages) == 3  # type: ignore[union-attr]


def test_ids_and_timestamps_default(db: Session) -> None:
    conv = _conversation(db)
    msg = Message(conversation_id=conv.id, role=Role.user, content="x", quote="q")
    db.add(msg)
    db.commit()
    assert len(msg.id) == 32 and msg.created_at is not None and msg.quote == "q"


def test_message_requires_existing_conversation(db: Session) -> None:
    db.add(Message(conversation_id="missing", role=Role.user, content="x"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_message_parent_must_exist(db: Session) -> None:
    conv = _conversation(db)
    db.add(Message(conversation_id=conv.id, parent_id="missing", role=Role.user, content="x"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_note_attaches_to_message(db: Session) -> None:
    conv = _conversation(db)
    origin = Message(conversation_id=conv.id, role=Role.assistant, content="o")
    db.add(origin)
    db.flush()
    leaf = Message(conversation_id=conv.id, parent_id=origin.id, role=Role.user, content="l")
    db.add(leaf)
    db.flush()
    db.add(Note(message_id=origin.id, source_leaf_id=leaf.id, content="summary"))
    db.commit()
    db.expire_all()
    notes = db.get(Message, origin.id).notes  # type: ignore[union-attr]
    assert [n.content for n in notes] == ["summary"]
    assert notes[0].source_leaf_id == leaf.id


def test_deleting_conversation_cascades(db: Session) -> None:
    conv = _conversation(db)
    db.add(Message(conversation_id=conv.id, role=Role.user, content="x"))
    db.commit()
    db.delete(conv)
    db.commit()
    assert db.query(Message).count() == 0
