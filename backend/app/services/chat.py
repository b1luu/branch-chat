"""Turning a tree path into an LLM prompt, and streaming a reply as SSE."""

import json
import logging
import os
from collections.abc import AsyncIterator, Mapping, Sequence

from sqlalchemy.orm import Session, sessionmaker

from app.api.schemas import MessageOut
from app.api.validation import clean_text
from app.db import repository
from app.db.errors import ConflictError
from app.db.models import Message, Note, Role
from app.llm import DEFAULT_MAX_TOKENS, ChatMessage, LLMProvider
from app.services import budget
from app.services.preview import make_preview

logger = logging.getLogger(__name__)

# Auto-titles are sidebar labels: short enough to fit one line of the list.
AUTO_TITLE_CHARS = 60


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
    """Persist the user's turn as a new child of `parent_id` (a branch if it has siblings).

    Retry after a failed reply: if the provider errored, the user turn was kept
    with no assistant child. Re-sending the same text under the same parent
    reuses that orphan instead of adding an identical sibling, so a retry
    doesn't show up as a spurious side branch in the tree.

    The parent must be an assistant or system message (409 otherwise): a user
    turn under a user turn would break the user/assistant alternation every
    path relies on (Anthropic's Messages API rejects it, and the question above
    would never get its own answer). To ask something else in place of a
    question, reply to that question's parent instead (the UI does this).

    A first question (parent is the root system message) also names the
    conversation if it still has the default title (Q33): otherwise every
    conversation in the sidebar reads "New conversation".
    """
    parent = repository.get_message(db, parent_id)
    if parent.role == Role.user:
        raise ConflictError(
            f"message {parent.id} is a user turn; reply to an assistant or system message"
        )
    orphan = repository.childless_user_turn(db, parent.id, content, quote)
    if orphan is not None:
        return orphan
    msg = Message(
        conversation_id=parent.conversation_id,
        parent_id=parent.id,
        role=Role.user,
        content=content,
        quote=quote,
    )
    db.add(msg)
    if parent.role == Role.system:
        title = make_preview(content, Role.user, AUTO_TITLE_CHARS)
        repository.title_from_first_turn(db, parent.conversation_id, title)
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

    Uses its own DB sessions because the response body runs after the request
    handler returns: one to load the path and one to save the reply, both
    closed before/after the model call so no connection waits on the LLM (Q29). The assistant node is written only once the stream
    completes, so a failed or abandoned generation leaves no half-written reply;
    an empty (or whitespace-only) answer is reported as `error` too.
    Saving the reply is guarded as well: if the commit fails, the client gets an
    `error` event instead of a stream that just stops (Q18). Unpaired UTF-16
    surrogates in the provider's text (a JSON-escaped half emoji) are replaced
    with U+FFFD before saving, since no database driver can encode them. This
    runs on the joined text, so an emoji whose two halves arrived in separate
    chunks is recombined into one code point rather than lost; tokens are
    streamed as-is (JSON-escaped, so the client joins them the same way).
    If the path is over `context_budget` (D2), old ancestors are condensed into
    one summary message first; nothing about that is persisted to the tree.
    """
    yield sse("user", MessageOut.model_validate(user_msg).model_dump(mode="json"))
    # Two short sessions, none open while the model runs (Q29): generation can
    # take minutes, and a session kept open across it pins a pooled connection
    # (an idle transaction on Postgres) per in-flight reply, so a few concurrent
    # replies would exhaust the pool and stall every other request.
    try:
        with session_factory() as db:
            messages = prompt_for(db, user_msg.id)
    except Exception:
        logger.exception("could not load the path for %s", user_msg.id)
        yield sse("error", {"detail": "Could not load the conversation; please retry"})
        return
    prompt = await budget.fit_to_budget(messages, provider, context_budget, summary_max_tokens)
    chunks: list[str] = []
    try:
        async for chunk in provider.stream(prompt, max_tokens=max_tokens):
            chunks.append(chunk)
            yield sse("token", {"text": chunk})
    except Exception as exc:  # provider/network failure: tell the client, persist nothing
        logger.exception("LLM provider %s failed", provider.name)
        yield sse("error", {"detail": f"LLM provider error: {exc}"})
        return
    content = clean_text("".join(chunks))
    if not content.strip():
        # Treat a blank answer like a failure: a blank assistant node would be a
        # dead bubble, and Anthropic rejects any later prompt containing an
        # empty assistant turn, poisoning the whole branch. Keeping the user
        # turn childless lets a retry reuse it (see create_user_node).
        logger.warning("LLM provider %s returned an empty reply", provider.name)
        yield sse("error", {"detail": "LLM returned an empty reply"})
        return
    assistant = Message(
        conversation_id=user_msg.conversation_id,
        parent_id=user_msg.id,
        role=Role.assistant,
        content=content,
    )
    try:
        with session_factory() as db:
            db.add(assistant)
            try:
                db.commit()
            except Exception:
                db.rollback()
                raise
            # Plain values for the `done` event, read while the session is open
            # (the object is detached once it closes).
            done = MessageOut.model_validate(assistant).model_dump(mode="json")
    except Exception:
        logger.exception("could not save the reply to %s", user_msg.id)
        yield sse("error", {"detail": "Could not save the reply; please retry"})
        return
    yield sse("done", done)
