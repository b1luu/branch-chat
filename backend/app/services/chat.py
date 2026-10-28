"""Turning a tree path into an LLM prompt, and streaming a reply as SSE."""

import json
import logging
import os
from collections.abc import AsyncIterator, Mapping, Sequence

from sqlalchemy.orm import Session, sessionmaker

from app.api.schemas import MessageOut
from app.db import repository
from app.db.models import Message, Note, Role
from app.llm import DEFAULT_MAX_TOKENS, ChatMessage, LLMProvider
from app.services import budget

logger = logging.getLogger(__name__)


def max_tokens_from_env() -> int:
    """Output cap per reply (cost guard). `LLM_MAX_TOKENS`, default 1024."""
    return int(os.getenv("LLM_MAX_TOKENS", str(DEFAULT_MAX_TOKENS)))


def format_user_content(content: str, quote: str | None) -> str:
    """Inline the highlighted excerpt so the model knows what "this" refers to."""
    if not quote:
        return content
    return (
        "Regarding this excerpt from your previous answer:\n"
        f'"""\n{quote}\n"""\n\n'
        f"{content}"
    )


NOTE_PREFIX = "Note merged back from a side discussion at this point:"


def format_note(note: Note) -> ChatMessage:
    return ChatMessage("system", f"{NOTE_PREFIX}\n{note.content}")


def build_prompt(
    path: Sequence[Message], notes: Mapping[str, Sequence[Note]] | None = None
) -> list[ChatMessage]:
    """Map a root→node path to provider messages, oldest first.

    The path comes from the recursive CTE, so it holds only this node's
    ancestors: sibling branches never reach the model. The one deliberate
    exception is merge-back (D1): a side branch's summary note sits on its fork
    origin and is inserted right after that message, so every descendant of the
    origin sees the side discussion's conclusion without its full transcript.
    A note is skipped on the branch it summarizes (its source leaf is on this
    path), since that branch already has the full turns.
    """
    notes = notes or {}
    on_path = {m.id for m in path}
    prompt: list[ChatMessage] = []
    for m in path:
        if m.role == Role.user:
            prompt.append(ChatMessage("user", format_user_content(m.content, m.quote)))
        elif m.role == Role.assistant:
            prompt.append(ChatMessage("assistant", m.content))
        else:
            prompt.append(ChatMessage("system", m.content))
        for note in notes.get(m.id, ()):
            if note.source_leaf_id not in on_path:
                prompt.append(format_note(note))
    return prompt


def prompt_for(db: Session, message_id: str) -> list[ChatMessage]:
    """The full LLM context for a node: its path plus notes merged onto it."""
    path = repository.get_path(db, message_id)
    return build_prompt(path, repository.notes_for(db, [m.id for m in path]))


def sse(event: str, data: object) -> str:
    """Encode one Server-Sent Event. JSON payloads keep newlines in tokens safe."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def create_user_node(db: Session, parent_id: str, content: str, quote: str | None) -> Message:
    """Persist the user's turn as a new child of `parent_id` (a branch if it has siblings)."""
    parent = repository.get_message(db, parent_id)
    msg = Message(
        conversation_id=parent.conversation_id,
        parent_id=parent.id,
        role=Role.user,
        content=content,
        quote=quote,
    )
    db.add(msg)
    db.commit()
    return msg


async def stream_reply(
    session_factory: sessionmaker[Session],
    provider: LLMProvider,
    user_msg: Message,
    max_tokens: int,
    context_budget: int = budget.DEFAULT_CONTEXT_TOKEN_BUDGET,
    summary_max_tokens: int = budget.DEFAULT_SUMMARY_MAX_TOKENS,
) -> AsyncIterator[str]:
    """SSE body for one reply: `user`, then `token`*, then `done` (or `error`).

    Uses its own DB session because the response body runs after the request
    handler returns. The assistant node is written only once the stream
    completes, so a failed or abandoned generation leaves no half-written reply.
    If the path is over `context_budget` (D2), old ancestors are condensed into
    one summary message first; nothing about that is persisted to the tree.
    """
    yield sse("user", MessageOut.model_validate(user_msg).model_dump(mode="json"))
    with session_factory() as db:
        prompt = await budget.fit_to_budget(
            prompt_for(db, user_msg.id), provider, context_budget, summary_max_tokens
        )
        chunks: list[str] = []
        try:
            async for chunk in provider.stream(prompt, max_tokens=max_tokens):
                chunks.append(chunk)
                yield sse("token", {"text": chunk})
        except Exception as exc:  # provider/network failure: tell the client, persist nothing
            logger.exception("LLM provider %s failed", provider.name)
            yield sse("error", {"detail": f"LLM provider error: {exc}"})
            return
        assistant = Message(
            conversation_id=user_msg.conversation_id,
            parent_id=user_msg.id,
            role=Role.assistant,
            content="".join(chunks),
        )
        db.add(assistant)
        db.commit()
        yield sse("done", MessageOut.model_validate(assistant).model_dump(mode="json"))
