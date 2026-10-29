"""Queries over conversations and the message tree."""

from collections.abc import Sequence

from sqlalchemy import literal, select
from sqlalchemy.orm import Session, aliased

from app.db.errors import ConflictError, NotFoundError
from app.db.models import Conversation, Message, Note, Role


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


def childless_user_turn(
    db: Session, parent_id: str, content: str, quote: str | None
) -> Message | None:
    """Newest user child of `parent_id` with this exact text and quote and no replies.

    That's the shape a failed reply leaves behind (B6 keeps the user turn,
    persists no assistant), so a retry can reuse it. Exact match only: any
    edit to the text is a genuinely new turn.
    """
    reply = aliased(Message)
    has_reply = select(reply.id).where(reply.parent_id == Message.id).exists()
    stmt = (
        select(Message)
        .where(
            Message.parent_id == parent_id,
            Message.role == Role.user,
            Message.content == content,
            Message.quote.is_(None) if quote is None else Message.quote == quote,
            ~has_reply,
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
    )
    return db.scalars(stmt).first()


def side_branch_start(db: Session, path: Sequence[Message]) -> int:
    """Index in `path` (root→leaf) of the first node of the innermost side branch.

    SPEC: a branch is any child of a message that already had a child. So the
    *oldest* child of a message continues its branch and each later sibling
    starts a side branch (the frontend's breadcrumb uses the same rule). The
    innermost side branch starts at the deepest path node that isn't its
    parent's oldest child; its parent is the fork origin the merge note goes on.
    Raises ConflictError if the whole path is on the main thread.
    """
    parent_ids = [m.parent_id for m in path if m.parent_id is not None]
    rows = db.execute(
        select(Message.parent_id, Message.id)
        .where(Message.parent_id.in_(parent_ids))
        .order_by(Message.parent_id, Message.created_at, Message.id)
    ).all()
    oldest: dict[str, str] = {}
    for parent_id, child_id in rows:
        oldest.setdefault(parent_id, child_id)
    for i in range(len(path) - 1, 0, -1):
        node = path[i]
        if node.parent_id is not None and oldest.get(node.parent_id) != node.id:
            return i
    raise ConflictError(f"message {path[-1].id} is on the main thread; nothing to merge")


def notes_for(db: Session, message_ids: Sequence[str]) -> dict[str, list[Note]]:
    """Merge-back notes attached to any of `message_ids`, oldest first per message."""
    if not message_ids:
        return {}
    notes = db.scalars(
        select(Note)
        .where(Note.message_id.in_(list(message_ids)))
        .order_by(Note.created_at, Note.id)
    ).all()
    by_message: dict[str, list[Note]] = {}
    for n in notes:
        by_message.setdefault(n.message_id, []).append(n)
    return by_message
