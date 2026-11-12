"""Merge back (D1): summarize a side branch into a note on its fork origin."""

import logging
from collections.abc import Mapping, Sequence

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.validation import clean_text
from app.config import env_int
from app.db import repository
from app.db.models import Message, Note, Role
from app.llm import ChatMessage, LLMProvider
from app.services import budget

logger = logging.getLogger(__name__)

SUMMARY_SYSTEM_PROMPT = (
    "You condense a side discussion so it can be pinned to the main conversation as a note. "
    "Write one to three plain sentences stating what was asked and what was concluded. "
    "No preamble, no headings."
)
DEFAULT_MERGE_MAX_TOKENS = 300
# Cap each transcript turn so a huge branch can't blow the summarizer's context.
TURN_CHARS = 4_000
# Floor for the whole-transcript cap, so a tiny CONTEXT_TOKEN_BUDGET still leaves room.
MIN_TRANSCRIPT_CHARS = 4_000
OMITTED_TURNS = "[… {n} earlier turns of the side discussion omitted …]"
NESTED_NOTE = "Note (merged back from a nested side discussion at this point): {text}"


class MergeError(Exception):
    """The provider failed or returned nothing; nothing was stored. Mapped to 502."""


def merge_max_tokens() -> int:
    """Output cap for summaries (`MERGE_MAX_TOKENS`, default 300): notes should be short."""
    return env_int("MERGE_MAX_TOKENS", DEFAULT_MERGE_MAX_TOKENS)


def _clip(text: str) -> str:
    return text if len(text) <= TURN_CHARS else text[: TURN_CHARS - 1] + "…"


def transcript_chars_for(context_budget: int, max_tokens: int) -> int:
    """Transcript cap (chars) so the summarizer call fits the context budget (D2).

    Leaves room for the summary itself (`max_tokens`) and the instructions,
    using the same ~4 chars/token estimate as the reply path.
    """
    room = context_budget - max_tokens - budget.estimate_tokens(SUMMARY_SYSTEM_PROMPT) - 50
    return max(MIN_TRANSCRIPT_CHARS, room * budget.CHARS_PER_TOKEN)


def _format_turn(m: Message, notes: Sequence[Note] = ()) -> str:
    """One transcript entry: the turn, then any nested merge notes pinned to it.

    Notes stay in the same entry as their message so `_fit_turns` keeps or
    drops them together (and its omitted count stays a count of turns).
    """
    speaker = "User" if m.role == Role.user else "Assistant"
    if m.quote:
        text = f'{speaker} (about the excerpt "{_clip(m.quote)}"): {_clip(m.content)}'
    else:
        text = f"{speaker}: {_clip(m.content)}"
    for note in notes:
        text += "\n" + NESTED_NOTE.format(text=_clip(note.content))
    return text


def _fit_turns(turns: list[str], max_chars: int) -> list[str]:
    """Drop middle turns until the transcript fits in `max_chars`.

    Each turn is already clipped, but a long side branch has unbounded turns.
    The first turn (the side question, i.e. what was asked) and the last one
    (the latest answer, i.e. what was concluded) are always kept; then as many
    of the most recent turns as fit, with a marker where turns were omitted.
    """
    if len(turns) <= 2 or sum(len(t) + 1 for t in turns) <= max_chars:
        return turns
    room = max_chars - len(turns[0]) - len(turns[-1]) - len(OMITTED_TURNS) - 10
    tail = [turns[-1]]
    for t in reversed(turns[1:-1]):
        if len(t) + 1 > room:
            break
        room -= len(t) + 1
        tail.insert(0, t)
    omitted = len(turns) - 1 - len(tail)
    if omitted == 0:
        return turns
    return [turns[0], OMITTED_TURNS.format(n=omitted), *tail]


def build_summary_prompt(
    origin: Message,
    branch: Sequence[Message],
    max_chars: int | None = None,
    notes: Mapping[str, Sequence[Note]] | None = None,
) -> list[ChatMessage]:
    """Summarizer input: instructions + one user turn holding the transcript.

    The transcript is a single user message (not replayed turns) so it works with
    every provider, including Anthropic's strict user/assistant alternation.
    The fork origin is included as context for what the side question refers to.
    With `max_chars`, the side discussion's turns are trimmed to fit (Q27):
    without it a long branch could exceed the model's context window, which
    is a 502 from Anthropic and silent front-truncation (losing the question)
    on Ollama.

    `notes` are merge-back notes on the branch's own messages (Q31): a nested
    side discussion that was merged into this branch is part of what this
    branch concluded (it is shown inline and in later context), so its note
    goes into the transcript right after the message it is pinned to. Like
    `chat.build_prompt`, a note whose source leaf is on this branch is skipped.
    """
    notes = notes or {}
    on_branch = {m.id for m in branch}
    head = f"Main conversation message the side discussion branched from:\n{_clip(origin.content)}"
    turns = [
        _format_turn(m, [n for n in notes.get(m.id, ()) if n.source_leaf_id not in on_branch])
        for m in branch
    ]
    if max_chars is not None:
        turns = _fit_turns(turns, max_chars - len(head))
    lines = [head, "\nSide discussion:", *turns, "\nSummarize the side discussion."]
    return [ChatMessage("system", SUMMARY_SYSTEM_PROMPT), ChatMessage("user", "\n".join(lines))]


async def merge_branch(
    db: Session,
    provider: LLMProvider,
    leaf_id: str,
    max_tokens: int,
    context_budget: int = budget.DEFAULT_CONTEXT_TOKEN_BUDGET,
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

    The transcript is trimmed to fit `context_budget` (see build_summary_prompt).

    Raises NotFoundError (404), ConflictError if `leaf_id` is on the main
    thread (409), or MergeError if the provider fails (502).
    """
    path = repository.get_path(db, leaf_id)
    start = repository.side_branch_start(db, path)
    existing = repository.note_for_leaf(db, leaf_id)
    if existing is not None:
        return existing, False
    origin, branch = path[start - 1], path[start:]
    origin_id = origin.id
    prompt = build_summary_prompt(
        origin,
        branch,
        max_chars=transcript_chars_for(context_budget, max_tokens),
        notes=repository.notes_for(db, [m.id for m in branch]),
    )
    # End the read transaction so no pooled connection is held while the
    # summarizer runs (Q29); nothing is pending, so this commit writes nothing.
    db.commit()
    try:
        chunks = [c async for c in provider.stream(prompt, max_tokens=max_tokens)]
    except Exception as exc:
        logger.exception("LLM provider %s failed while merging %s", provider.name, leaf_id)
        raise MergeError(f"LLM provider error: {exc}") from exc
    # Lone surrogates (half an emoji) can't be stored; see chat.stream_reply.
    summary = clean_text("".join(chunks)).strip()
    if not summary:
        raise MergeError("LLM returned an empty summary")
    note = Note(message_id=origin_id, source_leaf_id=leaf_id, content=summary)
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
