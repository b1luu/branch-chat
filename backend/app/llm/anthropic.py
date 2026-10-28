"""Anthropic provider over the Messages API with plain httpx (no SDK dependency).

Anthropic differs from the OpenAI shape in two ways handled here:
- System prompts go in a top-level `system` field, not in `messages`.
- `messages` must alternate user/assistant and start with `user`, so
  consecutive same-role turns are merged.

Prompt caching (D4): every reply's prompt is root→node, so a new reply shares
its whole ancestor prefix with the previous reply on that branch and with any
sibling branch forked from the same message. Anthropic caches a prompt prefix
up to each `cache_control` breakpoint (byte-exact; order is system → messages),
and a later request with the same prefix is billed at the cheap cache-read rate.
`build_payload` sets up to three breakpoints:

1. end of `system` (root prompt + any D2 ancestor summary),
2. end of the ancestor prefix (the turn before the newest user turn): an exact
   hit for every sibling branch replying to the same message,
3. end of the newest user turn: the next reply on this branch extends it.

Only *leading* system messages go into `system`. A system message later in the
path (a D1 merge note) is inlined as a tagged user-turn text at its position:
lifting it into `system` would change the very first bytes of the prompt and
invalidate the cache for every turn after it, and would also lose its place.
Prefixes shorter than the model's minimum cacheable length are simply not
cached; the markers are harmless there.
"""

import logging
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.llm.base import DEFAULT_MAX_TOKENS, ChatMessage, LLMProvider, ProviderConfigError
from app.llm.http_base import DEFAULT_TIMEOUT, ProviderError, iter_sse_json, raise_for_status

DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-5-5"
ANTHROPIC_VERSION = "2023-06-01"
EPHEMERAL: dict[str, str] = {"type": "ephemeral"}

logger = logging.getLogger(__name__)


def inline_system_note(text: str) -> str:
    """How a mid-conversation system message appears inside a user turn."""
    return f"<system_note>\n{text}\n</system_note>"


def to_anthropic_messages(
    messages: list[ChatMessage],
) -> tuple[str | None, list[dict[str, str]]]:
    """Split leading system text out, inline later system notes, merge same-role turns.

    Leading system messages (before the first user/assistant turn) become the
    top-level `system`. Later ones keep their position as a user-role note, so
    the cacheable prefix in front of them never changes (see module docstring).
    """
    lead = 0
    while lead < len(messages) and messages[lead].role == "system":
        lead += 1
    system = "\n\n".join(m.content for m in messages[:lead] if m.content) or None
    turns: list[dict[str, str]] = []
    for m in messages[lead:]:
        if not m.content and m.role == "system":
            continue
        role = "user" if m.role == "system" else m.role
        content = inline_system_note(m.content) if m.role == "system" else m.content
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"] += "\n\n" + content
        else:
            turns.append({"role": role, "content": content})
    return system, turns


def cached_block(text: str) -> list[dict[str, Any]]:
    """A single text block marked as a cache breakpoint."""
    return [{"type": "text", "text": text, "cache_control": dict(EPHEMERAL)}]


def log_usage(usage: dict[str, Any]) -> None:
    """Log input/cache token counts so cache hits are visible in the API logs."""
    if usage:
        logger.info(
            "anthropic usage: input=%s cache_write=%s cache_read=%s",
            usage.get("input_tokens", 0),
            usage.get("cache_creation_input_tokens", 0),
            usage.get("cache_read_input_tokens", 0),
        )


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        prompt_cache: bool | None = None,
    ) -> None:
        key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise ProviderConfigError("LLM_PROVIDER=anthropic requires ANTHROPIC_API_KEY")
        self.api_key = key
        self.model = model or os.getenv("ANTHROPIC_MODEL") or DEFAULT_ANTHROPIC_MODEL
        self.base_url = (
            base_url or os.getenv("ANTHROPIC_BASE_URL") or DEFAULT_ANTHROPIC_BASE_URL
        ).rstrip("/")
        self.timeout = timeout
        if prompt_cache is None:
            prompt_cache = os.getenv("ANTHROPIC_PROMPT_CACHE", "true").strip().lower() not in {
                "0",
                "false",
                "no",
                "off",
            }
        self.prompt_cache = prompt_cache

    def build_payload(self, messages: list[ChatMessage], max_tokens: int) -> dict[str, Any]:
        """The JSON body sent upstream (public so tests can inspect it).

        With prompt caching on, the system prompt and the last two turns carry
        `cache_control` breakpoints (at most 3 of Anthropic's 4 allowed).
        """
        system, turns = to_anthropic_messages(messages)
        out_turns: list[dict[str, Any]] = list(turns)
        if self.prompt_cache:
            for i in range(max(0, len(turns) - 2), len(turns)):
                out_turns[i] = {
                    "role": turns[i]["role"],
                    "content": cached_block(turns[i]["content"]),
                }
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": out_turns,
            "stream": True,
        }
        if system:
            payload["system"] = cached_block(system) if self.prompt_cache else system
        return payload

    async def stream(
        self, messages: list[ChatMessage], max_tokens: int = DEFAULT_MAX_TOKENS
    ) -> AsyncIterator[str]:
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        payload = self.build_payload(messages, max_tokens)
        try:
            async with (
                httpx.AsyncClient(timeout=self.timeout) as client,
                client.stream(
                    "POST", f"{self.base_url}/v1/messages", json=payload, headers=headers
                ) as response,
            ):
                await raise_for_status(response, "Anthropic")
                async for event in iter_sse_json(response):
                    kind = event.get("type")
                    if kind == "error":
                        detail = (event.get("error") or {}).get("message", event)
                        raise ProviderError(f"Anthropic stream error: {detail}")
                    if kind == "message_start":
                        log_usage((event.get("message") or {}).get("usage") or {})
                    elif kind == "content_block_delta":
                        delta = event.get("delta") or {}
                        if delta.get("type") == "text_delta" and delta.get("text"):
                            yield delta["text"]
                    elif kind == "message_stop":
                        return
        except httpx.HTTPError as exc:
            raise ProviderError(f"Could not reach Anthropic API: {exc}") from exc
