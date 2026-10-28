"""D2: context budget — token estimate + ancestor summarization with a tiny budget."""

import asyncio
from collections.abc import AsyncIterator, Coroutine, Iterator
from itertools import pairwise
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.db.models import Message
from app.db.session import make_session_factory
from app.llm import DEFAULT_MAX_TOKENS, ChatMessage, LLMProvider, MockProvider
from app.main import create_app
from app.services import budget
from app.services.budget import (
    OMITTED_PREFIX,
    SUMMARIZER_SYSTEM_PROMPT,
    SUMMARY_PREFIX,
    choose_cut,
    estimate_message,
    estimate_prompt,
    estimate_tokens,
    fit_to_budget,
)
from tests.test_reply_sse import FailingProvider, done, new_conversation, reply

SUMMARY_TEXT = "SUMMARY: the user greeted the assistant several times."


class SummaryAwareProvider(LLMProvider):
    """Records prompts; answers summarizer calls with SUMMARY_TEXT, others like MockProvider."""

    name = "summary-aware"

    def __init__(self, summary: str = SUMMARY_TEXT, fail_summaries: bool = False) -> None:
        self.calls: list[list[ChatMessage]] = []
        self.summary = summary
        self.fail_summaries = fail_summaries

    @staticmethod
    def is_summary_call(messages: list[ChatMessage]) -> bool:
        return bool(messages) and messages[0].content == SUMMARIZER_SYSTEM_PROMPT

    @property
    def summary_calls(self) -> list[list[ChatMessage]]:
        return [c for c in self.calls if self.is_summary_call(c)]

    @property
    def reply_calls(self) -> list[list[ChatMessage]]:
        return [c for c in self.calls if not self.is_summary_call(c)]

    async def stream(
        self, messages: list[ChatMessage], max_tokens: int = DEFAULT_MAX_TOKENS
    ) -> AsyncIterator[str]:
        self.calls.append(messages)
        if self.is_summary_call(messages):
            if self.fail_summaries:
                raise RuntimeError("summarizer down")
            yield self.summary
            return
        async for chunk in MockProvider().stream(messages, max_tokens):
            yield chunk


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    budget.clear_summary_cache()
    yield
    budget.clear_summary_cache()


def conversation(n_turns: int, chars: int = 200) -> list[ChatMessage]:
    """System root + n alternating user/assistant turns of `chars` chars each."""
    msgs = [ChatMessage("system", "You are a helpful assistant.")]
    for i in range(n_turns):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append(ChatMessage(role, f"turn {i} " + "x" * chars))
    return msgs


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


# --- token estimate ------------------------------------------------------------


def test_estimate_tokens_is_chars_over_four_rounded_up() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2
    assert estimate_tokens("x" * 400) == 100


def test_estimate_prompt_adds_per_message_overhead() -> None:
    msgs = [ChatMessage("user", "abcd"), ChatMessage("assistant", "abcd")]
    assert estimate_message(msgs[0]) == 1 + budget.MESSAGE_OVERHEAD_TOKENS
    assert estimate_prompt(msgs) == 2 * (1 + budget.MESSAGE_OVERHEAD_TOKENS)


def test_budget_env_defaults_and_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONTEXT_TOKEN_BUDGET", raising=False)
    monkeypatch.delenv("CONTEXT_SUMMARY_MAX_TOKENS", raising=False)
    assert budget.context_budget_from_env() == 60_000
    assert budget.summary_max_tokens_from_env() == 500
    monkeypatch.setenv("CONTEXT_TOKEN_BUDGET", "123")
    monkeypatch.setenv("CONTEXT_SUMMARY_MAX_TOKENS", "45")
    assert budget.context_budget_from_env() == 123
    assert budget.summary_max_tokens_from_env() == 45


# --- cut selection -------------------------------------------------------------


def test_under_budget_is_returned_unchanged_without_llm_call() -> None:
    msgs = conversation(5)
    provider = SummaryAwareProvider()
    out = run(fit_to_budget(msgs, provider, budget=estimate_prompt(msgs)))
    assert out is msgs
    assert provider.calls == []


def test_cut_keeps_tail_within_budget_and_starts_on_user_turn() -> None:
    msgs = conversation(41)  # ends on a user turn
    for b in (300, 500, 900, 1500):
        cut = choose_cut(msgs, head=1, budget=b, reserve=50)
        assert 1 < cut < len(msgs)
        assert msgs[cut].role == "user"
        assert estimate_prompt(msgs[:1]) + 50 + estimate_prompt(msgs[cut:]) <= b


def test_cut_is_stable_across_consecutive_turns() -> None:
    """Rounding to CUT_STEP means the summarized prefix doesn't move every reply."""
    cuts = [choose_cut(conversation(n), 1, budget=1000, reserve=50) for n in range(21, 61, 2)]
    changes = sum(1 for a, b in pairwise(cuts) if a != b)
    assert changes <= len(cuts) // 2  # moves at most every other turn, not every turn
    assert cuts == sorted(cuts)


def test_newest_turn_is_kept_even_if_it_alone_exceeds_budget() -> None:
    msgs = [*conversation(4), ChatMessage("user", "y" * 10_000)]
    provider = SummaryAwareProvider()
    out = run(fit_to_budget(msgs, provider, budget=100, summary_max_tokens=10))
    assert out[0] == msgs[0]
    assert out[1].role == "system" and out[1].content.startswith(SUMMARY_PREFIX)
    assert out[-1] == msgs[-1]
    assert len(out) == 3


def test_nothing_to_condense_when_only_the_new_turn_follows_the_root() -> None:
    msgs = [ChatMessage("system", "sys"), ChatMessage("user", "y" * 10_000)]
    provider = SummaryAwareProvider()
    assert run(fit_to_budget(msgs, provider, budget=10)) == msgs
    assert provider.calls == []


# --- summarization -------------------------------------------------------------


def test_over_budget_condenses_oldest_into_one_system_summary() -> None:
    msgs = conversation(31, chars=100)
    provider = SummaryAwareProvider()
    out = run(fit_to_budget(msgs, provider, budget=500, summary_max_tokens=50))
    assert out[0] == msgs[0]  # root system prompt kept verbatim
    assert out[1] == ChatMessage("system", f"{SUMMARY_PREFIX}\n{SUMMARY_TEXT}")
    tail = out[2:]
    assert tail == msgs[len(msgs) - len(tail) :]  # newest turns verbatim, in order
    assert tail[0].role == "user"
    assert estimate_prompt(out) <= 500
    # The summarizer saw exactly the condensed turns, oldest first.
    [summary_prompt] = provider.summary_calls
    transcript = summary_prompt[1].content
    condensed = msgs[1 : len(msgs) - len(tail)]
    assert condensed and all(m.content in transcript for m in condensed)
    positions = [transcript.index(m.content) for m in condensed]
    assert positions == sorted(positions)
    assert all(m.content not in transcript for m in tail)


def test_summary_is_cached_while_the_cut_stays_put() -> None:
    reserve = 50 + estimate_tokens(SUMMARY_PREFIX) + budget.MESSAGE_OVERHEAD_TOKENS
    sizes = range(21, 61, 2)
    cuts = {n: choose_cut(conversation(n), 1, 800, reserve) for n in sizes}
    n1, n2 = next((a, a + 2) for a in sizes if cuts[a] == cuts.get(a + 2))
    provider = SummaryAwareProvider()
    first = run(fit_to_budget(conversation(n1), provider, budget=800, summary_max_tokens=50))
    second = run(fit_to_budget(conversation(n2), provider, budget=800, summary_max_tokens=50))
    assert first[1] == second[1]
    assert len(provider.summary_calls) == 1  # second reply reused the cached summary


def test_cache_is_keyed_by_content() -> None:
    provider = SummaryAwareProvider()
    run(fit_to_budget(conversation(31), provider, budget=800, summary_max_tokens=50))
    run(fit_to_budget(conversation(31, chars=201), provider, budget=800, summary_max_tokens=50))
    assert len(provider.summary_calls) == 2


def test_summarizer_failure_drops_old_turns_with_marker() -> None:
    msgs = conversation(31)
    provider = SummaryAwareProvider(fail_summaries=True)
    out = run(fit_to_budget(msgs, provider, budget=800, summary_max_tokens=50))
    assert out[1].role == "system" and out[1].content.startswith(OMITTED_PREFIX)
    assert out[-1] == msgs[-1]
    assert estimate_prompt(out) <= 800


def test_empty_summary_is_treated_as_failure_and_not_cached() -> None:
    provider = SummaryAwareProvider(summary="   ")
    out = run(fit_to_budget(conversation(31), provider, budget=800, summary_max_tokens=50))
    assert out[1].content.startswith(OMITTED_PREFIX)
    run(fit_to_budget(conversation(31), provider, budget=800, summary_max_tokens=50))
    assert len(provider.summary_calls) == 2


def test_summarizer_transcript_is_bounded() -> None:
    old = [ChatMessage("user", "z" * 50_000) for _ in range(10)]
    prompt = budget.build_summarizer_prompt(old, max_chars=5_000)
    assert len(prompt) == 2 and prompt[0].role == "system" and prompt[1].role == "user"
    assert len(prompt[1].content) < 6_000
    assert "earlier turns omitted" in prompt[1].content


# --- reply endpoint ------------------------------------------------------------


@pytest.fixture
def provider() -> SummaryAwareProvider:
    return SummaryAwareProvider()


@pytest.fixture
def bclient(engine: Engine, provider: SummaryAwareProvider) -> Iterator[TestClient]:
    with TestClient(create_app(make_session_factory(engine), provider)) as c:
        yield c


def long_chat(client: TestClient, turns: int) -> str:
    leaf = new_conversation(client)["root_message_id"]
    for i in range(turns):
        leaf = done(reply(client, leaf, f"question {i} " + "q" * 400))["id"]
    return leaf


def test_reply_with_tiny_budget_sends_summary_and_recent_turns(
    bclient: TestClient,
    provider: SummaryAwareProvider,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    leaf = long_chat(bclient, 8)  # under the default budget: no summaries yet
    assert provider.summary_calls == []
    monkeypatch.setenv("CONTEXT_TOKEN_BUDGET", "600")
    monkeypatch.setenv("CONTEXT_SUMMARY_MAX_TOKENS", "40")
    rows_before = db.scalar(select(func.count()).select_from(Message))
    events = reply(bclient, leaf, "final question")
    prompt = provider.reply_calls[-1]
    assert prompt[0] == ChatMessage("system", "You are a helpful assistant.")
    assert prompt[1] == ChatMessage("system", f"{SUMMARY_PREFIX}\n{SUMMARY_TEXT}")
    assert prompt[-1] == ChatMessage("user", "final question")
    assert estimate_prompt(prompt) <= 600
    assert "question 0 " not in " ".join(m.content for m in prompt[2:])
    # The mock reports the condensed context size, and only user+assistant got stored.
    assert f"with {len(prompt)} context messages" in done(events)["content"]
    assert db.scalar(select(func.count()).select_from(Message)) == rows_before + 2


def test_reply_under_budget_sends_full_path(
    bclient: TestClient, provider: SummaryAwareProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CONTEXT_TOKEN_BUDGET", "100000")
    leaf = long_chat(bclient, 3)
    reply(bclient, leaf, "next")
    assert provider.summary_calls == []
    assert len(provider.reply_calls[-1]) == 1 + 2 * 3 + 1


def test_reply_still_streams_when_summarizer_fails(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = SummaryAwareProvider(fail_summaries=True)
    with TestClient(create_app(make_session_factory(engine), provider)) as c:
        leaf = long_chat(c, 8)
        monkeypatch.setenv("CONTEXT_TOKEN_BUDGET", "600")
        events = reply(c, leaf, "final question")
    assert events[-1][0] == "done"
    assert provider.reply_calls[-1][1].content.startswith(OMITTED_PREFIX)


def test_reply_provider_error_still_reported_with_budget(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CONTEXT_TOKEN_BUDGET", "10")
    with TestClient(create_app(make_session_factory(engine), FailingProvider())) as c:
        conv = new_conversation(c)
        events = reply(c, conv["root_message_id"], "hi " * 100)
    assert events[-1][0] == "error"
