"""Merge back (D1): summarize a side branch into a note on its fork origin."""

import logging
import os
from collections.abc import Sequence

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import repository
from app.db.models import Message, Note, Role
from app.llm import ChatMessage, LLMProvider

logger = logging.getLogger(__name__)

SUMMARY_SYSTEM_PROMPT = (
    "You condense a side discussion so it can be pinned to the main conversation as a note. "
    "Write one to three plain sentences stating what was asked and what was concluded. "
    "No preamble, no headings."
)
# Cap each transcript turn so a huge branch can't blow the summarizer's context.
TURN_CHARS = 4_000


class MergeError(Exception):
    """The provider failed or returned nothing; nothing was stored. Mapped to 502."""


def merge_max_tokens() -> int:
    """Output cap for summaries (`MERGE_MAX_TOKENS`, default 300): notes should be short."""
    return int(os.getenv("MERGE_MAX_TOKENS", "300"))


def _clip(text: str) -> str:
    return text if len(text) <= TURN_CHARS else text[: TURN_CHARS - 1] + "…"


def build_summary_prompt(origin: Message, branch: Sequence[Message]) -> list[ChatMessage]:
    """Summarizer input: instructions + one user turn holding the transcript.

    The transcript is a single user message (not replayed turns) so it works with
    every provider, including Anthropic's strict user/assistant alternation.
    The fork origin is included as context for what the side question refers to.
    """
    lines = [
        f"Main conversation message the side discussion branched from:\n{_clip(origin.content)}"
    ]
    lines.append("\nSide discussion:")
    for m in branch:
        speaker = "User" if m.role == Role.user else "Assistant"
        if m.quote:
            lines.append(f'{speaker} (about the excerpt "{_clip(m.quote)}"): {_clip(m.content)}')
        else:
            lines.append(f"{speaker}: {_clip(m.content)}")
    lines.append("\nSummarize the side discussion.")
    return [ChatMessage("system", SUMMARY_SYSTEM_PROMPT), ChatMessage("user", "\n".join(lines))]


async def merge_branch(
    db: Session, provider: LLMProvider, leaf_id: str, max_tokens: int
) -> tuple[Note, bool]:
    """Summarize the innermost side branch ending at `leaf_id` and store the note.

    Returns `(note, created)`. The branch runs from the first node of the
    innermost side branch down to `leaf_id`; the note goes on that node's parent
    (the fork origin), where `build_prompt` shows it to every later descendant
    of the origin.

    Idempotent per leaf: tree messages never change, so merging a leaf that was
    already merged returns the existing note (`created=False`) without calling
    the LLM. A unique index on `notes.source_leaf_id` closes the race where two
    merges of the same leaf run at once: the loser's insert fails and it returns
    the winner's note instead of storing a duplicate.

    Merging a deeper leaf of a branch that grew is a different leaf, so it adds
    a second note next to the earlier one (unchanged D1 behavior).

    Raises NotFoundError (404), ConflictError if `leaf_id` is on the main
    thread (409), or MergeError if the provider fails (502).
    """
    path = repository.get_path(db, leaf_id)
    start = repository.side_branch_start(db, path)
    existing = repository.note_for_leaf(db, leaf_id)
    if existing is not None:
        return existing, False
    origin, branch = path[start - 1], path[start:]
    prompt = build_summary_prompt(origin, branch)
    try:
        chunks = [c async for c in provider.stream(prompt, max_tokens=max_tokens)]
    except Exception as exc:
        logger.exception("LLM provider %s failed while merging %s", provider.name, leaf_id)
        raise MergeError(f"LLM provider error: {exc}") from exc
    summary = "".join(chunks).strip()
    if not summary:
        raise MergeError("LLM returned an empty summary")
    note = Note(message_id=origin.id, source_leaf_id=leaf_id, content=summary)
    db.add(note)
    try:
        db.commit()
    except IntegrityError:
        # A concurrent merge of the same leaf committed first: use its note.
        db.rollback()
        winner = repository.note_for_leaf(db, leaf_id)
        if winner is None:  # the conflict wasn't ours to resolve
            raise
        return winner, False
    return note, True
