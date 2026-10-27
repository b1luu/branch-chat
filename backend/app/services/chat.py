"""Turning a tree path into an LLM prompt, and streaming a reply as SSE."""

import json
import logging
import os
from collections.abc import AsyncIterator, Sequence

from sqlalchemy.orm import Session, sessionmaker

from app.api.schemas import MessageOut
from app.db import repository
from app.db.models import Message, Role
from app.llm import DEFAULT_MAX_TOKENS, ChatMessage, LLMProvider

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


def build_prompt(path: Sequence[Message]) -> list[ChatMessage]:
    """Map a root→node path to provider messages, oldest first.

    The path comes from the recursive CTE, so it holds only this node's
    ancestors: sibling branches never reach the model.
    """
    prompt: list[ChatMessage] = []
    for m in path:
        if m.role == Role.user:
            prompt.append(ChatMessage("user", format_user_content(m.content, m.quote)))
        elif m.role == Role.assistant:
            prompt.append(ChatMessage("assistant", m.content))
        else:
            prompt.append(ChatMessage("system", m.content))
    return prompt


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
) -> AsyncIterator[str]:
    """SSE body for one reply: `user`, then `token`*, then `done` (or `error`).

    Uses its own DB session because the response body runs after the request
    handler returns. The assistant node is written only once the stream
    completes, so a failed or abandoned generation leaves no half-written reply.
    """
    yield sse("user", MessageOut.model_validate(user_msg).model_dump(mode="json"))
    with session_factory() as db:
        prompt = build_prompt(repository.get_path(db, user_msg.id))
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
