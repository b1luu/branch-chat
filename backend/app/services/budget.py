"""Context budget (D2): keep a long root→node prompt under `CONTEXT_TOKEN_BUDGET`.

When the prompt built from the tree path is estimated to exceed the budget, the
oldest ancestors are replaced by one system message that summarizes them, and
the most recent turns are kept verbatim:

    [root system] [summary of turns 1..cut) ] [turns cut..end, verbatim]

Design points worth knowing:

* **Token estimate, not a tokenizer.** ~4 chars/token plus a small per-message
  overhead. It is provider-agnostic, needs no dependency, and errs on the side
  of summarizing a little early. The budget is a cost guard, not an exact limit.
* **Stable cut points.** The cut index is rounded up to a multiple of
  `CUT_STEP` messages, so the summarized prefix stays identical for several
  turns in a row instead of shifting by two messages every reply.
* **Summary cache.** Summaries are cached in-process, keyed by the provider
  and a hash of the exact summarized messages. Thanks to stable cuts, the next
  few replies reuse the summary instead of paying for another LLM call, and
  sibling branches that share the same old ancestors share one summary too.
* **Never block the reply.** If the summarizer fails or returns nothing, the
  old turns are dropped with a short system marker so the reply still works.
"""

import hashlib
import logging
import math
import os
from collections import OrderedDict
from collections.abc import Sequence

from app.llm import ChatMessage, LLMProvider

logger = logging.getLogger(__name__)

DEFAULT_CONTEXT_TOKEN_BUDGET = 60_000
DEFAULT_SUMMARY_MAX_TOKENS = 500
CHARS_PER_TOKEN = 4
MESSAGE_OVERHEAD_TOKENS = 4  # role markers / separators each provider adds per turn
CUT_STEP = 8
TURN_CHARS = 4_000
CACHE_SIZE = 256

SUMMARY_PREFIX = "Summary of the earlier part of this conversation (older turns condensed):"
OMITTED_PREFIX = "Earlier parts of this conversation were omitted to fit the context budget"
SUMMARIZER_SYSTEM_PROMPT = (
    "You condense the beginning of a long conversation so it can replace those turns in the "
    "model's context. Keep facts, decisions, names, numbers, open questions and anything the "
    "user asked to remember. Be concise. No preamble, no headings."
)

_cache: "OrderedDict[str, str]" = OrderedDict()


def context_budget_from_env() -> int:
    """`CONTEXT_TOKEN_BUDGET` (estimated tokens), default 60k."""
    return int(os.getenv("CONTEXT_TOKEN_BUDGET", str(DEFAULT_CONTEXT_TOKEN_BUDGET)))


def summary_max_tokens_from_env() -> int:
    """`CONTEXT_SUMMARY_MAX_TOKENS`: output cap for the ancestor summary, default 500."""
    return int(os.getenv("CONTEXT_SUMMARY_MAX_TOKENS", str(DEFAULT_SUMMARY_MAX_TOKENS)))


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def estimate_message(m: ChatMessage) -> int:
    return estimate_tokens(m.content) + MESSAGE_OVERHEAD_TOKENS


def estimate_prompt(messages: Sequence[ChatMessage]) -> int:
    return sum(estimate_message(m) for m in messages)


def clear_summary_cache() -> None:
    _cache.clear()


def choose_cut(messages: Sequence[ChatMessage], head: int, budget: int, reserve: int) -> int:
    """Index where the verbatim tail starts, or `head` if nothing needs summarizing.

    The tail is everything from the cut to the end; it must fit in
    `budget - reserve - head tokens`. The last message (the new user turn) is
    always kept, even if it alone is over budget. The cut is rounded up to a
    multiple of CUT_STEP (stability, see module docstring) and then moved
    forward to a user turn so the tail never opens with an assistant reply
    (Anthropic requires the first turn to be from the user).
    """
    n = len(messages)
    if n - head < 2 or estimate_prompt(messages) <= budget:
        return head  # nothing older than the newest turn to condense
    room = budget - reserve - estimate_prompt(messages[:head])
    cut, used = n, 0
    while cut > head + 1 and used + estimate_message(messages[cut - 1]) <= room:
        cut -= 1
        used += estimate_message(messages[cut])
    if cut == n:  # even the newest turn doesn't fit: keep it anyway
        cut = n - 1
    cut = min(n - 1, head + math.ceil((cut - head) / CUT_STEP) * CUT_STEP)
    while cut < n - 1 and messages[cut].role != "user":
        cut += 1
    return max(cut, head + 1)


def build_summarizer_prompt(old: Sequence[ChatMessage], max_chars: int) -> list[ChatMessage]:
    """One system + one user turn holding a transcript of the turns to condense.

    Each turn is clipped to TURN_CHARS and the transcript to `max_chars`
    (keeping its most recent part), so the summarizer call itself stays bounded.
    """
    speaker = {"user": "User", "assistant": "Assistant", "system": "Note"}
    lines = []
    for m in old:
        text = m.content if len(m.content) <= TURN_CHARS else m.content[: TURN_CHARS - 1] + "…"
        lines.append(f"{speaker[m.role]}: {text}")
    transcript = "\n\n".join(lines)
    if len(transcript) > max_chars:
        transcript = "[…earlier turns omitted…]\n\n" + transcript[-max_chars:]
    user = f"Conversation so far:\n\n{transcript}\n\nSummarize it."
    return [ChatMessage("system", SUMMARIZER_SYSTEM_PROMPT), ChatMessage("user", user)]


def _cache_key(provider: LLMProvider, old: Sequence[ChatMessage]) -> str:
    h = hashlib.sha256(provider.name.encode())
    for m in old:
        h.update(b"\x00" + m.role.encode() + b"\x01" + m.content.encode())
    return h.hexdigest()


async def summarize(
    provider: LLMProvider, old: Sequence[ChatMessage], max_tokens: int, max_chars: int
) -> str | None:
    """Summary text for `old` (cached), or None if the provider failed or returned nothing."""
    key = _cache_key(provider, old)
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    prompt = build_summarizer_prompt(old, max_chars)
    try:
        text = "".join([c async for c in provider.stream(prompt, max_tokens=max_tokens)]).strip()
    except Exception:
        logger.exception("LLM provider %s failed while summarizing ancestors", provider.name)
        return None
    if not text:
        return None
    _cache[key] = text
    if len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)
    return text


async def fit_to_budget(
    messages: list[ChatMessage],
    provider: LLMProvider,
    budget: int,
    summary_max_tokens: int = DEFAULT_SUMMARY_MAX_TOKENS,
) -> list[ChatMessage]:
    """Return `messages` unchanged if within budget, else with old turns condensed.

    A leading system message (the conversation's root prompt) is always kept
    as-is. The summary replaces everything between it and the cut.
    """
    if not messages or estimate_prompt(messages) <= budget:
        return messages
    head = 1 if messages[0].role == "system" else 0
    reserve = summary_max_tokens + estimate_tokens(SUMMARY_PREFIX) + MESSAGE_OVERHEAD_TOKENS
    cut = choose_cut(messages, head, budget, reserve)
    if cut <= head:
        return messages
    old = messages[head:cut]
    summary = await summarize(
        provider, old, summary_max_tokens, max_chars=max(budget, 1_000) * CHARS_PER_TOKEN
    )
    if summary is None:
        marker = ChatMessage("system", f"{OMITTED_PREFIX} ({len(old)} messages).")
    else:
        marker = ChatMessage("system", f"{SUMMARY_PREFIX}\n{summary}")
    logger.info(
        "context budget %d exceeded: condensed %d of %d messages (%s)",
        budget,
        len(old),
        len(messages),
        "summary" if summary else "dropped",
    )
    return [*messages[:head], marker, *messages[cut:]]
