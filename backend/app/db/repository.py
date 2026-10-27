"""Read queries over the message tree."""

from sqlalchemy import literal, select
from sqlalchemy.orm import Session, aliased

from app.db.errors import NotFoundError
from app.db.models import Message


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
