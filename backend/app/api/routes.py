"""v1 conversation and tree endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_llm
from app.api.schemas import (
    PREVIEW_CHARS,
    ConversationCreate,
    ConversationOut,
    MessageOut,
    ReplyCreate,
    TreeNode,
    TreeOut,
)
from app.db import repository
from app.db.models import Message
from app.llm import LLMProvider
from app.services import chat

router = APIRouter(prefix="/api")
DB = Annotated[Session, Depends(get_db)]
LLM = Annotated[LLMProvider, Depends(get_llm)]


def _preview(content: str) -> str:
    """Single-line, length-capped preview for sidebar nodes."""
    flat = " ".join(content.split())
    return flat if len(flat) <= PREVIEW_CHARS else flat[: PREVIEW_CHARS - 1] + "…"


@router.post("/conversations", status_code=status.HTTP_201_CREATED)
def create_conversation(body: ConversationCreate, db: DB) -> ConversationOut:
    conv, root = repository.create_conversation(db, body.title, body.system_prompt)
    db.commit()
    return ConversationOut(
        id=conv.id, title=conv.title, created_at=conv.created_at, root_message_id=root.id
    )


@router.get("/conversations")
def list_conversations(db: DB) -> list[ConversationOut]:
    return [
        ConversationOut(id=c.id, title=c.title, created_at=c.created_at, root_message_id=root_id)
        for c, root_id in repository.list_conversations(db)
    ]


@router.get("/conversations/{conversation_id}/tree")
def get_tree(conversation_id: str, db: DB) -> TreeOut:
    messages: list[Message] = repository.get_tree(db, conversation_id)
    return TreeOut(
        conversation_id=conversation_id,
        nodes=[
            TreeNode(
                id=m.id,
                parent_id=m.parent_id,
                role=m.role,
                preview=_preview(m.content),
                created_at=m.created_at,
            )
            for m in messages
        ],
    )


@router.get("/messages/{message_id}/path")
def get_message_path(message_id: str, db: DB) -> list[MessageOut]:
    return [MessageOut.model_validate(m) for m in repository.get_path(db, message_id)]


@router.post("/messages/{message_id}/reply")
def reply(
    message_id: str, body: ReplyCreate, request: Request, db: DB, llm: LLM
) -> StreamingResponse:
    """Add a user turn under `message_id` and stream the assistant's answer as SSE.

    Replying to a message that already has children creates a new branch.
    The user node is committed (and a missing parent 404s) before streaming
    starts, so errors that need a status code happen before the 200.
    """
    user_msg = chat.create_user_node(db, message_id, body.content, body.quote)
    return StreamingResponse(
        chat.stream_reply(
            request.app.state.session_factory, llm, user_msg, chat.max_tokens_from_env()
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
