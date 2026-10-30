"""one merge note per source leaf

Drops exact duplicate notes (same `source_leaf_id`, created by double-submitted
merges before this revision), keeping the oldest, then adds a unique index so
the database itself rejects a second note for the same leaf. NULL leaf ids
(leaf deleted, FK set null) stay allowed any number of times on both SQLite and
Postgres.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-30 19:50:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM notes
            WHERE source_leaf_id IS NOT NULL
              AND EXISTS (
                SELECT 1 FROM notes AS older
                WHERE older.source_leaf_id = notes.source_leaf_id
                  AND (older.created_at < notes.created_at
                       OR (older.created_at = notes.created_at AND older.id < notes.id))
              )
            """
        )
    )
    with op.batch_alter_table("notes", schema=None) as batch_op:
        batch_op.create_index("uq_notes_source_leaf_id", ["source_leaf_id"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("notes", schema=None) as batch_op:
        batch_op.drop_index("uq_notes_source_leaf_id")
