"""Pick the LLM provider from the `LLM_PROVIDER` env var (default: mock)."""

import os

from app.llm.base import LLMProvider, ProviderConfigError
from app.llm.mock import MockProvider

KNOWN_PROVIDERS = ("mock", "ollama", "anthropic")

__all__ = ["KNOWN_PROVIDERS", "ProviderConfigError", "get_provider"]


def get_provider(name: str | None = None) -> LLMProvider:
    """Build a provider by name, falling back to `LLM_PROVIDER`, then "mock".

    Mock is the default so a fresh checkout and every test run never touch a
    real model or network. Real providers are imported lazily so the mock path
    never loads HTTP client code.
    """
    chosen = (name or os.getenv("LLM_PROVIDER") or "mock").strip().lower()
    if chosen == "mock":
        return MockProvider()
    if chosen == "ollama":
        from app.llm.ollama import OllamaProvider

        return OllamaProvider()
    if chosen == "anthropic":
        from app.llm.anthropic import AnthropicProvider

        return AnthropicProvider()
    raise ProviderConfigError(
        f"Unknown LLM_PROVIDER {chosen!r}; expected one of {', '.join(KNOWN_PROVIDERS)}"
    )
