"""Ollama provider via its OpenAI-compatible `/v1/chat/completions` endpoint.

Using the OpenAI-compatible API (not Ollama's native `/api/chat`) means the
same code would work against vLLM, LM Studio, or OpenAI itself.
"""

import os
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.llm.base import DEFAULT_MAX_TOKENS, ChatMessage, LLMProvider
from app.llm.http_base import (
    DEFAULT_TIMEOUT,
    ProviderError,
    StreamState,
    iter_sse_json,
    raise_for_status,
)

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434/v1"
DEFAULT_OLLAMA_MODEL = "llama3.1:8b"


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = (
            base_url or os.getenv("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_BASE_URL
        ).rstrip("/")
        self.model = model or os.getenv("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL
        self.timeout = timeout

    def build_payload(self, messages: list[ChatMessage], max_tokens: int) -> dict[str, Any]:
        """The JSON body sent upstream (public so tests can inspect it)."""
        return {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": max_tokens,
            "stream": True,
        }

    async def stream(
        self, messages: list[ChatMessage], max_tokens: int = DEFAULT_MAX_TOKENS
    ) -> AsyncIterator[str]:
        url = f"{self.base_url}/chat/completions"
        payload = self.build_payload(messages, max_tokens)
        # A body that just stops (proxy idle timeout, server restart) is not an
        # HTTP error, so without this check a half reply would be saved as if it
        # were complete. A finished OpenAI-style stream has a `finish_reason` on
        # its last choice and then `[DONE]`; either one counts as finished.
        state = StreamState()
        finished = False
        try:
            async with (
                httpx.AsyncClient(timeout=self.timeout) as client,
                client.stream("POST", url, json=payload) as response,
            ):
                await raise_for_status(response, "Ollama")
                async for event in iter_sse_json(response, state):
                    if "error" in event:
                        raise ProviderError(f"Ollama stream error: {event['error']}")
                    for choice in event.get("choices") or []:
                        text = (choice.get("delta") or {}).get("content")
                        if text:
                            yield text
                        if choice.get("finish_reason"):
                            finished = True
        except httpx.HTTPError as exc:
            raise ProviderError(f"Could not reach Ollama at {self.base_url}: {exc}") from exc
        if not (finished or state.saw_done):
            raise ProviderError("Ollama stream ended before the reply finished")
