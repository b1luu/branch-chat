"""Queries over conversations and the message tree."""

from sqlalchemy import literal, select
from sqlalchemy.orm import Session, aliased

from app.db.errors import NotFoundError
from app.db.models import Conversation, Message, Role


def get_message(db: Session, message_id: str) -> Message:
    msg = db.get(Message, message_id)
    if msg is None:
        raise NotFoundError("message", message_id)
    return msg


def get_path(db: Session, message_id: str) -> list[Message]:
    """Return the messages from the root down to `message_id`, oldest first.

    This is the LLM context for a node: only its ancestors, never siblings or
    cousins, so side branches stay isolated from each other.

    Implemented as one recursive CTE that walks *up* the tree:

        WITH RECURSIVE path(id, parent_id, depth) AS (
            SELECT id, parent_id, 0 FROM messages WHERE id = :message_id   -- anchor
            UNION ALL
            SELECT m.id, m.parent_id, path.depth + 1                       -- step
            FROM messages m JOIN path ON m.id = path.parent_id
        )
        SELECT messages.* FROM messages JOIN path USING (id) ORDER BY depth DESC;

    Each step follows one `parent_id` link, so the recursion stops at the root
    (parent_id IS NULL). `depth` counts distance from the target, so ordering by
    depth descending yields root first. Standard SQL: runs on SQLite and Postgres.
    Cost is O(depth) index lookups on the primary key, regardless of tree size.
    """
    anchor = select(
        Message.id.label("id"),
        Message.parent_id.label("parent_id"),
        literal(0).label("depth"),
    ).where(Message.id == message_id)
    path = anchor.cte("path", recursive=True)

    parent = aliased(Message)
    step = select(
        parent.id,
        parent.parent_id,
        (path.c.depth + 1).label("depth"),
    ).join(path, parent.id == path.c.parent_id)
    path = path.union_all(step)

    rows = db.scalars(
        select(Message).join(path, Message.id == path.c.id).order_by(path.c.depth.desc())
    ).all()
    if not rows:
        raise NotFoundError("message", message_id)
    return list(rows)


def create_conversation(
    db: Session, title: str, system_prompt: str
) -> tuple[Conversation, Message]:
    """Create a conversation with its root system message.

    Every conversation starts with one root node (the system prompt) so the
    first user turn is just `POST /messages/{root_id}/reply`, like any other
    turn. That keeps "reply" the only way messages are created.
    Flushes but does not commit; the caller owns the transaction.
    """
    conv = Conversation(title=title)
    db.add(conv)
    db.flush()
    root = Message(conversation_id=conv.id, parent_id=None, role=Role.system, content=system_prompt)
    db.add(root)
    db.flush()
    return conv, root


def list_conversations(db: Session) -> list[tuple[Conversation, str | None]]:
    """All conversations, newest first, each with its root message id."""
    root = aliased(Message)
    stmt = (
        select(Conversation, root.id)
        .outerjoin(root, (root.conversation_id == Conversation.id) & root.parent_id.is_(None))
        .order_by(Conversation.created_at.desc(), Conversation.id)
    )
    return [(conv, root_id) for conv, root_id in db.execute(stmt).all()]


def get_tree(db: Session, conversation_id: str) -> list[Message]:
    """Every message in a conversation, oldest first (parents before children).

    The client rebuilds the tree from `parent_id`; one flat indexed query is
    cheaper than nesting on the server.
    """
    if db.get(Conversation, conversation_id) is None:
        raise NotFoundError("conversation", conversation_id)
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at, Message.id)
    )
    return list(db.scalars(stmt).all())
