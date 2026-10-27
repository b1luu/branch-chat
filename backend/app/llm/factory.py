"""Pick the LLM provider from the `LLM_PROVIDER` env var (default: mock)."""

import os

from app.llm.base import LLMProvider
from app.llm.mock import MockProvider

KNOWN_PROVIDERS = ("mock", "ollama", "anthropic")


class ProviderConfigError(ValueError):
    """LLM_PROVIDER names an unknown or not-yet-available provider."""


def get_provider(name: str | None = None) -> LLMProvider:
    """Build a provider by name, falling back to `LLM_PROVIDER`, then "mock".

    Mock is the default so a fresh checkout and every test run never touch a
    real model or network.
    """
    chosen = (name or os.getenv("LLM_PROVIDER") or "mock").strip().lower()
    if chosen == "mock":
        return MockProvider()
    if chosen in KNOWN_PROVIDERS:
        # Real providers land in B7; fail loudly rather than silently mocking.
        raise ProviderConfigError(f"LLM provider {chosen!r} is not implemented yet")
    raise ProviderConfigError(
        f"Unknown LLM_PROVIDER {chosen!r}; expected one of {', '.join(KNOWN_PROVIDERS)}"
    )
