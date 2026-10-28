"""Anthropic provider over the Messages API with plain httpx (no SDK dependency).

Anthropic differs from the OpenAI shape in two ways handled here:
- System prompts go in a top-level `system` field, not in `messages`.
- `messages` must alternate user/assistant and start with `user`, so
  consecutive same-role turns are merged.
Prompt caching of the shared ancestor prefix is D4; this module keeps the
payload builder separate so D4 only has to touch `build_payload`.
"""

import os
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.llm.base import DEFAULT_MAX_TOKENS, ChatMessage, LLMProvider, ProviderConfigError
from app.llm.http_base import DEFAULT_TIMEOUT, ProviderError, iter_sse_json, raise_for_status

DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-5-5"
ANTHROPIC_VERSION = "2023-06-01"


def to_anthropic_messages(
    messages: list[ChatMessage],
) -> tuple[str | None, list[dict[str, str]]]:
    """Split out system text and merge consecutive same-role turns."""
    system_parts = [m.content for m in messages if m.role == "system" and m.content]
    turns: list[dict[str, str]] = []
    for m in messages:
        if m.role == "system":
            continue
        if turns and turns[-1]["role"] == m.role:
            turns[-1]["content"] += "\n\n" + m.content
        else:
            turns.append({"role": m.role, "content": m.content})
    system = "\n\n".join(system_parts) or None
    return system, turns


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
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

    def build_payload(self, messages: list[ChatMessage], max_tokens: int) -> dict[str, Any]:
        """The JSON body sent upstream (public so tests can inspect it)."""
        system, turns = to_anthropic_messages(messages)
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": turns,
            "stream": True,
        }
        if system:
            payload["system"] = system
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
                    if kind == "content_block_delta":
                        delta = event.get("delta") or {}
                        if delta.get("type") == "text_delta" and delta.get("text"):
                            yield delta["text"]
                    elif kind == "message_stop":
                        return
        except httpx.HTTPError as exc:
            raise ProviderError(f"Could not reach Anthropic API: {exc}") from exc
