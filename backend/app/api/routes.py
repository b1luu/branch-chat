"""v1 conversation and tree endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_llm
from app.api.schemas import (
    PREVIEW_CHARS,
    ConversationCreate,
    ConversationOut,
    MessageOut,
    NoteOut,
    PathMessageOut,
    ReplyCreate,
    TreeNode,
    TreeOut,
)
from app.db import repository
from app.db.models import Message
from app.llm import LLMProvider
from app.services import budget, chat, merge

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
def get_message_path(message_id: str, db: DB) -> list[PathMessageOut]:
    """Root→node messages, each with the merge-back notes pinned to it."""
    path = repository.get_path(db, message_id)
    notes = repository.notes_for(db, [m.id for m in path])
    return [
        PathMessageOut(
            **MessageOut.model_validate(m).model_dump(),
            notes=[NoteOut.model_validate(n) for n in notes.get(m.id, [])],
        )
        for m in path
    ]


@router.post("/branches/{leaf_id}/merge", status_code=status.HTTP_201_CREATED)
async def merge_branch(leaf_id: str, db: DB, llm: LLM, response: Response) -> NoteOut:
    """Summarize the side branch ending at `leaf_id` into a note on its fork origin.

    Not streamed: the note is short and only useful once complete. 201 with the
    new note, or 200 with the existing one if this leaf was already merged
    (idempotent, no LLM call). 404 for an unknown leaf, 409 if the leaf is on
    the main thread, 502 if the LLM fails.
    """
    try:
        note, created = await merge.merge_branch(db, llm, leaf_id, merge.merge_max_tokens())
    except merge.MergeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    if not created:
        response.status_code = status.HTTP_200_OK
    return NoteOut.model_validate(note)


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
            request.app.state.session_factory,
            llm,
            user_msg,
            chat.max_tokens_from_env(),
            context_budget=budget.context_budget_from_env(),
            summary_max_tokens=budget.summary_max_tokens_from_env(),
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
