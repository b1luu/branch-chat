"""Pydantic request/response models for the v1 API."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."
PREVIEW_CHARS = 80


def _not_blank(value: str) -> str:
    """`min_length=1` alone lets "   " through; a blank turn or title is never useful."""
    if not value.strip():
        raise ValueError("must not be blank")
    return value


class ConversationCreate(BaseModel):
    title: str = Field(default="New conversation", min_length=1, max_length=200)
    system_prompt: str = Field(default=DEFAULT_SYSTEM_PROMPT, min_length=1, max_length=10_000)

    @field_validator("title")
    @classmethod
    def _clean_title(cls, value: str) -> str:
        # Titles are single-line labels: trim and collapse whitespace.
        return " ".join(_not_blank(value).split())

    @field_validator("system_prompt")
    @classmethod
    def _check_system_prompt(cls, value: str) -> str:
        return _not_blank(value)


class ReplyCreate(BaseModel):
    content: str = Field(min_length=1, max_length=50_000)
    # Text highlighted in the parent assistant message ("Ask about this").
    quote: str | None = Field(default=None, max_length=20_000)

    @field_validator("content")
    @classmethod
    def _check_content(cls, value: str) -> str:
        # Content is kept verbatim (code blocks care about whitespace), just not blank.
        return _not_blank(value)

    @field_validator("quote")
    @classmethod
    def _blank_quote_is_none(cls, value: str | None) -> str | None:
        # An empty highlight means "no quote", not a quote branch about nothing.
        return value if value and value.strip() else None


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    created_at: datetime
    root_message_id: str | None


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    parent_id: str | None
    role: str
    content: str
    quote: str | None
    created_at: datetime


class NoteOut(BaseModel):
    """A merge-back summary, attached to the message its side branch forked from."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    message_id: str
    source_leaf_id: str | None
    content: str
    created_at: datetime


class PathMessageOut(MessageOut):
    """A message on a root→node path, with any merge-back notes attached to it."""

    notes: list[NoteOut] = []


class TreeNode(BaseModel):
    """A lightweight node for the sidebar: no full content, just a preview."""

    id: str
    parent_id: str | None
    role: str
    preview: str
    created_at: datetime


class TreeOut(BaseModel):
    conversation_id: str
    nodes: list[TreeNode]
