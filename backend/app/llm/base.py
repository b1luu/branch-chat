"""Provider-agnostic LLM interface.

Every provider exposes one async streaming method. The reply endpoint (B6) only
ever talks to this interface, so tests use `MockProvider` and production swaps
in Ollama or Anthropic via the `LLM_PROVIDER` env var.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal

ChatRole = Literal["system", "user", "assistant"]
DEFAULT_MAX_TOKENS = 1024


@dataclass(frozen=True)
class ChatMessage:
    """One turn of prompt context, already flattened from the DB path."""

    role: ChatRole
    content: str


class LLMProvider(ABC):
    name: str

    @abstractmethod
    def stream(
        self, messages: list[ChatMessage], max_tokens: int = DEFAULT_MAX_TOKENS
    ) -> AsyncIterator[str]:
        """Yield the assistant reply as text chunks, in order.

        `messages` is the full context, oldest first. `max_tokens` caps output
        length (a cost guard). Implementations are async generators.
        """
