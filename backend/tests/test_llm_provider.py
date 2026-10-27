import pytest

from app.llm import ChatMessage, LLMProvider, MockProvider, ProviderConfigError, get_provider
from app.llm.mock import mock_reply


async def _collect(provider: LLMProvider, messages: list[ChatMessage], **kw: int) -> list[str]:
    return [chunk async for chunk in provider.stream(messages, **kw)]


CONTEXT = [
    ChatMessage("system", "You are helpful."),
    ChatMessage("user", "What is a monad?"),
    ChatMessage("assistant", "A monoid in the category of endofunctors."),
    ChatMessage("user", "Explain   that\nsimply"),
]


async def test_mock_streams_multiple_chunks_that_join_to_reply() -> None:
    chunks = await _collect(MockProvider(), CONTEXT)
    assert len(chunks) > 1
    assert "".join(chunks) == mock_reply(CONTEXT)


async def test_mock_reply_mentions_context_count_and_last_user_message() -> None:
    text = "".join(await _collect(MockProvider(), CONTEXT))
    assert text == 'Mock reply to "Explain that simply" with 4 context messages.'


async def test_mock_is_deterministic() -> None:
    a = await _collect(MockProvider(), CONTEXT)
    b = await _collect(MockProvider(), CONTEXT)
    assert a == b


async def test_mock_count_changes_with_context() -> None:
    text = "".join(await _collect(MockProvider(), CONTEXT[:2]))
    assert "with 2 context messages" in text


async def test_mock_handles_no_user_message() -> None:
    text = "".join(await _collect(MockProvider(), [ChatMessage("system", "s")]))
    assert text == 'Mock reply to "" with 1 context messages.'


async def test_mock_respects_max_tokens() -> None:
    chunks = await _collect(MockProvider(), CONTEXT, max_tokens=3)
    assert len(chunks) == 3
    assert mock_reply(CONTEXT).startswith("".join(chunks))
    assert await _collect(MockProvider(), CONTEXT, max_tokens=0) == []


def test_factory_defaults_to_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert isinstance(get_provider(), MockProvider)


def test_factory_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", " Mock ")
    assert get_provider().name == "mock"


def test_factory_explicit_name_wins_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "nonsense")
    assert isinstance(get_provider("mock"), MockProvider)


def test_factory_unknown_provider_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "gpt-banana")
    with pytest.raises(ProviderConfigError, match="Unknown LLM_PROVIDER"):
        get_provider()


@pytest.mark.parametrize("name", ["ollama", "anthropic"])
def test_factory_real_providers_not_yet_available(name: str) -> None:
    with pytest.raises(ProviderConfigError, match="not implemented yet"):
        get_provider(name)
