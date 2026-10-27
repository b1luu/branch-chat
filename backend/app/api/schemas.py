"""Pydantic request/response models for the v1 API."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."
PREVIEW_CHARS = 80


class ConversationCreate(BaseModel):
    title: str = Field(default="New conversation", min_length=1, max_length=200)
    system_prompt: str = Field(default=DEFAULT_SYSTEM_PROMPT, min_length=1, max_length=10_000)


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
