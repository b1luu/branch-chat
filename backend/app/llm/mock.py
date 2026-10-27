"""Deterministic provider used in every automated test (no network)."""

from collections.abc import AsyncIterator

from app.llm.base import DEFAULT_MAX_TOKENS, ChatMessage, LLMProvider

ECHO_CHARS = 60


def mock_reply(messages: list[ChatMessage]) -> str:
    """The full text MockProvider would stream for `messages`.

    It states how many context messages it received, so tests can check that
    the right path (and nothing else) was sent to the model.
    """
    last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
    echo = " ".join(last_user.split())[:ECHO_CHARS]
    return f'Mock reply to "{echo}" with {len(messages)} context messages.'


class MockProvider(LLMProvider):
    name = "mock"

    async def stream(
        self, messages: list[ChatMessage], max_tokens: int = DEFAULT_MAX_TOKENS
    ) -> AsyncIterator[str]:
        # One "token" per word (keeping the trailing space) so streaming is
        # multi-chunk and max_tokens truncation is observable in tests.
        words = mock_reply(messages).split(" ")
        for i, word in enumerate(words[: max(max_tokens, 0)]):
            yield word if i == len(words) - 1 else word + " "
