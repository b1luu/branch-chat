"""Numeric env settings (Q36): blank means default, bad values fail at startup.

Before Q36 every numeric setting was parsed with a bare `int(os.getenv(...))`
on each request. `LLM_MAX_TOKENS=` (blank, the way `.env.example` leaves
`ANTHROPIC_API_KEY=`) or a typo like `60k` made every reply a 500 *after* the
question was saved, and every merge a 500, while the API looked healthy.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.api.ratelimit import RateLimitConfig
from app.config import ConfigError, env_float, env_int
from app.db.models import Message
from app.db.session import make_session_factory
from app.llm import DEFAULT_MAX_TOKENS, MockProvider
from app.main import create_app
from app.services import budget, chat, merge

NUMERIC_SETTINGS = (
    "LLM_MAX_TOKENS",
    "MERGE_MAX_TOKENS",
    "CONTEXT_TOKEN_BUDGET",
    "CONTEXT_SUMMARY_MAX_TOKENS",
    "RATE_LIMIT_PER_MINUTE",
    "RATE_LIMIT_BURST",
)

# --- env_int / env_float -------------------------------------------------------


def test_env_int_unset_and_blank_mean_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("Q36_X", raising=False)
    assert env_int("Q36_X", 7) == 7
    for blank in ("", "   ", "\t"):
        monkeypatch.setenv("Q36_X", blank)
        assert env_int("Q36_X", 7) == 7


def test_env_int_parses_with_surrounding_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("Q36_X", " 512 ")
    assert env_int("Q36_X", 7) == 512


@pytest.mark.parametrize("raw", ["60k", "1.5", "abc", "1e3", "0x10"])
def test_env_int_rejects_non_integers_naming_the_variable(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("Q36_X", raw)
    with pytest.raises(ConfigError, match=r"Q36_X must be an integer >= 1") as info:
        env_int("Q36_X", 7)
    assert repr(raw) in str(info.value)


@pytest.mark.parametrize("raw", ["0", "-5"])
def test_env_int_enforces_minimum(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("Q36_X", raw)
    with pytest.raises(ConfigError, match="Q36_X"):
        env_int("Q36_X", 7)
    assert env_int("Q36_X", 7, minimum=-10) == int(raw)


def test_env_float(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("Q36_F", "")
    assert env_float("Q36_F", 2.5) == 2.5
    monkeypatch.setenv("Q36_F", "0.5")
    assert env_float("Q36_F", 2.5) == 0.5
    for bad in ("nan", "inf", "-1", "fast"):
        monkeypatch.setenv("Q36_F", bad)
        with pytest.raises(ConfigError, match="Q36_F"):
            env_float("Q36_F", 2.5)


# --- the real settings ---------------------------------------------------------


def test_blank_settings_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in NUMERIC_SETTINGS:
        monkeypatch.setenv(name, "")
    assert chat.max_tokens_from_env() == DEFAULT_MAX_TOKENS
    assert merge.merge_max_tokens() == 300
    assert budget.context_budget_from_env() == budget.DEFAULT_CONTEXT_TOKEN_BUDGET
    assert budget.summary_max_tokens_from_env() == budget.DEFAULT_SUMMARY_MAX_TOKENS
    config = RateLimitConfig.from_env()
    assert (config.per_minute, config.burst) == (20.0, 10)


@pytest.mark.parametrize("name", NUMERIC_SETTINGS)
def test_bad_setting_fails_app_startup_with_its_name(
    monkeypatch: pytest.MonkeyPatch, engine: Engine, name: str
) -> None:
    monkeypatch.setenv(name, "60k")
    with pytest.raises(ConfigError, match=name):
        create_app(make_session_factory(engine), MockProvider())


@pytest.mark.parametrize("name", ["LLM_MAX_TOKENS", "CONTEXT_TOKEN_BUDGET", "RATE_LIMIT_BURST"])
def test_zero_setting_fails_app_startup(
    monkeypatch: pytest.MonkeyPatch, engine: Engine, name: str
) -> None:
    # 0 output tokens is a 400 from Anthropic; a burst of 0 would 429 every request.
    monkeypatch.setenv(name, "0")
    with pytest.raises(ConfigError, match=name):
        create_app(make_session_factory(engine), MockProvider())


def test_blank_max_tokens_reply_streams_normally(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    monkeypatch.setenv("LLM_MAX_TOKENS", "")
    monkeypatch.setenv("CONTEXT_TOKEN_BUDGET", " ")
    root = client.post("/api/conversations", json={}).json()["root_message_id"]
    response = client.post(f"/api/messages/{root}/reply", json={"content": "hi"})
    assert response.status_code == 200
    assert "event: done" in response.text


def test_setting_broken_after_startup_saves_no_question(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, db: Session
) -> None:
    """Settings are read before the question is stored, so a bad one leaves no orphan."""
    root = client.post("/api/conversations", json={}).json()["root_message_id"]
    monkeypatch.setenv("LLM_MAX_TOKENS", "lots")
    with pytest.raises(ConfigError, match="LLM_MAX_TOKENS"):
        client.post(f"/api/messages/{root}/reply", json={"content": "hi"})
    assert db.scalar(select(func.count()).select_from(Message)) == 1  # only the root
