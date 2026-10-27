import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.errors import NotFoundError
from app.db.models import Conversation, Message, Role
from app.db.repository import get_path
from app.main import create_app


class Tree:
    """Builds a message tree in one conversation."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.conv = Conversation(title="t")
        db.add(self.conv)
        db.flush()

    def add(self, content: str, parent: Message | None = None) -> Message:
        role = Role.user if parent is None or parent.role == Role.assistant else Role.assistant
        msg = Message(
            conversation_id=self.conv.id,
            parent_id=parent.id if parent else None,
            role=role,
            content=content,
        )
        self.db.add(msg)
        self.db.flush()
        return msg


def _contents(messages: list[Message]) -> list[str]:
    return [m.content for m in messages]


def test_linear_chain_is_root_first(any_db: Session) -> None:
    t = Tree(any_db)
    m1 = t.add("1")
    m2 = t.add("2", m1)
    m3 = t.add("3", m2)
    m4 = t.add("4", m3)
    assert _contents(get_path(any_db, m4.id)) == ["1", "2", "3", "4"]
    assert _contents(get_path(any_db, m2.id)) == ["1", "2"]


def test_branch_siblings_do_not_leak(any_db: Session) -> None:
    t = Tree(any_db)
    root = t.add("root")
    answer = t.add("answer", root)
    main = t.add("main follow-up", answer)
    side = t.add("side question", answer)
    side_reply = t.add("side reply", side)
    main_reply = t.add("main reply", main)

    assert _contents(get_path(any_db, side_reply.id)) == [
        "root",
        "answer",
        "side question",
        "side reply",
    ]
    assert _contents(get_path(any_db, main_reply.id)) == [
        "root",
        "answer",
        "main follow-up",
        "main reply",
    ]


def test_other_conversations_do_not_leak(any_db: Session) -> None:
    a = Tree(any_db)
    b = Tree(any_db)
    a_leaf = a.add("a2", a.add("a1"))
    b.add("b2", b.add("b1"))
    assert _contents(get_path(any_db, a_leaf.id)) == ["a1", "a2"]


def test_root_only(any_db: Session) -> None:
    root = Tree(any_db).add("only")
    path = get_path(any_db, root.id)
    assert _contents(path) == ["only"]
    assert path[0].parent_id is None


def test_deep_chain(any_db: Session) -> None:
    t = Tree(any_db)
    node = t.add("0")
    for i in range(1, 200):
        node = t.add(str(i), node)
    assert _contents(get_path(any_db, node.id)) == [str(i) for i in range(200)]


def test_missing_id_raises_not_found(any_db: Session) -> None:
    with pytest.raises(NotFoundError):
        get_path(any_db, "does-not-exist")


def test_not_found_maps_to_404() -> None:
    app = create_app()

    @app.get("/_raise")
    def _raise() -> None:
        raise NotFoundError("message", "x")

    response = TestClient(app).get("/_raise")
    assert response.status_code == 404
    assert response.json() == {"detail": "message x not found"}
