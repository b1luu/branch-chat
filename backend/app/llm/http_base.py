"""Shared plumbing for providers that stream over HTTP (Ollama, Anthropic).

Both real providers speak Server-Sent Events: the response body is a series of
`data: <json>` lines. This module turns that byte stream into parsed JSON
events so each provider only has to know its own event schema.
"""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

# Connect fast, but allow long gaps between tokens (a local 8B model on a cold
# GPU can take a while to emit its first token).
DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=120.0, write=30.0, pool=5.0)


class ProviderError(RuntimeError):
    """The upstream model API returned an error or an unreadable stream."""


async def raise_for_status(response: httpx.Response, provider: str) -> None:
    """Raise `ProviderError` with the upstream body (truncated) on non-2xx."""
    if response.is_success:
        return
    body = (await response.aread()).decode("utf-8", errors="replace")[:500]
    raise ProviderError(f"{provider} returned HTTP {response.status_code}: {body}")


async def iter_sse_json(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    """Yield each `data:` payload as a dict; stop at OpenAI's `[DONE]` sentinel.

    `event:` / `id:` / comment lines are ignored: both APIs repeat the event
    type inside the JSON, so the data line alone is enough.
    """
    async for line in response.aiter_lines():
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if not payload:
            continue
        if payload == "[DONE]":
            return
        try:
            event = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"Malformed stream event: {payload[:200]}") from exc
        if isinstance(event, dict):
            yield event
