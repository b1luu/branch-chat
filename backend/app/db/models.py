"""ORM models for the conversation tree.

A conversation is a tree of messages linked by `parent_id` (an adjacency list).
Adjacency lists are cheap to write (insert one row per message) and the
root-to-node path is read with a recursive CTE (see B3).
"""

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import DateTime, Dialect, ForeignKey, Index, String, Text, TypeDecorator
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _new_id() -> str:
    return uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """`DateTime(timezone=True)` that always hands back UTC-aware datetimes.

    Postgres stores `timestamptz` and returns aware values, but SQLite has no
    time zone type: it stores the text and returns *naive* datetimes. Without
    this, a row read back from SQLite serialized as `...T19:47:42.18` while a
    freshly created one gave `...T19:47:42.18Z`, so the same note had two
    different timestamps and a browser would parse the naive one as local time.
    Values are normalized to UTC on the way in, and naive values coming out are
    tagged UTC (they were written as UTC). Same column type for migrations.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(UTC)
        return value

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Role(StrEnum):
    user = "user"
    assistant = "assistant"
    system = "system"


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    title: Mapped[str] = mapped_column(String(200), default="New conversation")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    # NULL for the root message. Indexed because the path CTE joins on it.
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True, nullable=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    # Highlighted text from the parent assistant message for "Ask about this" branches.
    quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    parent: Mapped["Message | None"] = relationship(
        remote_side="Message.id", back_populates="children"
    )
    children: Mapped[list["Message"]] = relationship(back_populates="parent")
    notes: Mapped[list["Note"]] = relationship(
        back_populates="message", cascade="all, delete-orphan", foreign_keys="Note.message_id"
    )


class Note(Base):
    """Merge-back summary of a side branch, attached to the branch's fork-origin message.

    At most one note per source leaf (unique index): messages are immutable, so
    merging the same leaf twice would summarize the exact same turns. The index
    also makes a double-submitted merge race-safe (see services/merge.py).
    """

    __tablename__ = "notes"
    __table_args__ = (Index("uq_notes_source_leaf_id", "source_leaf_id", unique=True),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    message_id: Mapped[str] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    # The leaf of the branch that was summarized.
    source_leaf_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)

    message: Mapped[Message] = relationship(back_populates="notes", foreign_keys=[message_id])
