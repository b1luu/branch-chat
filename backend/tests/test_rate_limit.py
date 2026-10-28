"""D3: per-IP token-bucket rate limiting on the reply and merge endpoints."""

import math
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.api.ratelimit import (
    MAX_TRACKED_CLIENTS,
    RateLimitConfig,
    TokenBucketLimiter,
    client_key,
    is_limited,
)
from app.db.models import Message
from app.db.session import make_session_factory
from app.llm import MockProvider
from app.main import create_app
from tests.test_reply_sse import done, new_conversation, reply


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


# --- token bucket --------------------------------------------------------------


def test_bucket_allows_burst_then_reports_wait(clock: FakeClock) -> None:
    limiter = TokenBucketLimiter(per_minute=6, burst=3, clock=clock)  # 1 token / 10 s
    assert [limiter.acquire("a") for _ in range(3)] == [None, None, None]
    assert limiter.acquire("a") == pytest.approx(10.0)
    clock.advance(4)
    assert limiter.acquire("a") == pytest.approx(6.0)


def test_bucket_refills_over_time_up_to_burst(clock: FakeClock) -> None:
    limiter = TokenBucketLimiter(per_minute=60, burst=2, clock=clock)  # 1 token / s
    assert limiter.acquire("a") is None
    assert limiter.acquire("a") is None
    assert limiter.acquire("a") is not None
    clock.advance(1)
    assert limiter.acquire("a") is None
    clock.advance(3600)  # long idle: capped at burst, not 3600 tokens
    assert [limiter.acquire("a") for _ in range(3)][:2] == [None, None]
    assert limiter.acquire("a") is not None


def test_buckets_are_per_client(clock: FakeClock) -> None:
    limiter = TokenBucketLimiter(per_minute=1, burst=1, clock=clock)
    assert limiter.acquire("a") is None
    assert limiter.acquire("a") is not None
    assert limiter.acquire("b") is None


def test_zero_rate_never_refills(clock: FakeClock) -> None:
    limiter = TokenBucketLimiter(per_minute=0, burst=1, clock=clock)
    assert limiter.acquire("a") is None
    assert limiter.acquire("a") == math.inf


def test_idle_full_buckets_are_pruned(clock: FakeClock) -> None:
    limiter = TokenBucketLimiter(per_minute=60, burst=1, clock=clock)
    for i in range(MAX_TRACKED_CLIENTS):
        limiter.acquire(f"ip{i}")
    assert limiter.tracked_clients == MAX_TRACKED_CLIENTS
    clock.advance(5)  # everyone refilled
    limiter.acquire("newcomer")
    assert limiter.tracked_clients == 1


# --- request matching / client key ---------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "limited"),
    [
        ("POST", "/api/messages/abc/reply", True),
        ("POST", "/api/branches/abc/merge", True),
        ("GET", "/api/messages/abc/path", False),
        ("POST", "/api/conversations", False),
        ("GET", "/api/messages/abc/reply", False),
        ("GET", "/healthz", False),
    ],
)
def test_only_llm_endpoints_are_limited(method: str, path: str, limited: bool) -> None:
    assert is_limited({"type": "http", "method": method, "path": path}) is limited


def test_client_key_uses_peer_unless_proxy_trusted() -> None:
    scope = {
        "client": ("10.0.0.5", 1234),
        "headers": [(b"x-forwarded-for", b"6.6.6.6, 203.0.113.7")],
    }
    assert client_key(scope, trust_proxy=False) == "10.0.0.5"
    # Last hop = what our own proxy saw; the first one is client-controlled.
    assert client_key(scope, trust_proxy=True) == "203.0.113.7"
    assert client_key({"client": ("10.0.0.5", 1), "headers": []}, trust_proxy=True) == "10.0.0.5"
    assert client_key({"headers": []}, trust_proxy=False) == "unknown"


def test_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "RATE_LIMIT_ENABLED",
        "RATE_LIMIT_PER_MINUTE",
        "RATE_LIMIT_BURST",
        "RATE_LIMIT_TRUST_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)
    assert RateLimitConfig.from_env() == RateLimitConfig(True, 20.0, 10, False)
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "0")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "5")
    monkeypatch.setenv("RATE_LIMIT_BURST", "2")
    monkeypatch.setenv("RATE_LIMIT_TRUST_PROXY", "true")
    assert RateLimitConfig.from_env() == RateLimitConfig(False, 5.0, 2, True)


# --- endpoints -------------------------------------------------------------------


@pytest.fixture
def limited_client(engine: Engine, clock: FakeClock) -> Iterator[TestClient]:
    config = RateLimitConfig(enabled=True, per_minute=6, burst=2)
    limiter = TokenBucketLimiter(config.per_minute, config.burst, clock=clock)
    app = create_app(make_session_factory(engine), MockProvider(), config, limiter)
    with TestClient(app) as c:
        yield c


def test_reply_returns_429_with_retry_after_when_exhausted(
    limited_client: TestClient, clock: FakeClock, db: Session
) -> None:
    root = new_conversation(limited_client)["root_message_id"]
    a1 = done(reply(limited_client, root, "one"))
    done(reply(limited_client, a1["id"], "two"))
    rows = db.scalar(select(func.count()).select_from(Message))

    r = limited_client.post(f"/api/messages/{a1['id']}/reply", json={"content": "three"})
    assert r.status_code == 429
    assert r.headers["retry-after"] == "10"
    assert "Rate limit" in r.json()["detail"]
    assert db.scalar(select(func.count()).select_from(Message)) == rows  # nothing persisted

    clock.advance(3)
    r = limited_client.post(f"/api/messages/{a1['id']}/reply", json={"content": "three"})
    assert r.status_code == 429 and r.headers["retry-after"] == "7"

    clock.advance(7)
    done(reply(limited_client, a1["id"], "three"))


def test_reads_are_never_limited(limited_client: TestClient) -> None:
    conv = new_conversation(limited_client)
    root = conv["root_message_id"]
    reply(limited_client, root, "one")
    reply(limited_client, root, "two")
    for _ in range(20):
        assert limited_client.get(f"/api/conversations/{conv['id']}/tree").status_code == 200
        assert limited_client.get(f"/api/messages/{root}/path").status_code == 200
    assert limited_client.post("/api/conversations", json={}).status_code == 201


def test_merge_shares_the_bucket_and_is_limited(limited_client: TestClient) -> None:
    root = new_conversation(limited_client)["root_message_id"]
    a1 = done(reply(limited_client, root, "main"))  # oldest child: main thread
    side = done(reply(limited_client, root, "side"))  # later sibling: side branch
    r = limited_client.post(f"/api/branches/{side['id']}/merge")
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1
    assert a1["id"] != side["id"]


def test_clients_behind_trusted_proxy_get_separate_buckets(
    engine: Engine, clock: FakeClock
) -> None:
    config = RateLimitConfig(enabled=True, per_minute=6, burst=1, trust_proxy=True)
    limiter = TokenBucketLimiter(config.per_minute, config.burst, clock=clock)
    with TestClient(create_app(make_session_factory(engine), MockProvider(), config, limiter)) as c:
        root = new_conversation(c)["root_message_id"]

        def post(ip: str) -> int:
            return c.post(
                f"/api/messages/{root}/reply",
                json={"content": "hi"},
                headers={"X-Forwarded-For": ip},
            ).status_code

        assert post("198.51.100.1") == 200
        assert post("198.51.100.1") == 429
        assert post("198.51.100.2") == 200


def test_disabled_limiter_adds_no_middleware(engine: Engine) -> None:
    app = create_app(make_session_factory(engine), MockProvider(), RateLimitConfig(enabled=False))
    assert app.state.rate_limiter is None
    with TestClient(app) as c:
        root = new_conversation(c)["root_message_id"]
        for i in range(15):
            reply(c, root, f"q{i}")


def test_rate_limit_enabled_from_env_by_default(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RATE_LIMIT_ENABLED")
    monkeypatch.setenv("RATE_LIMIT_BURST", "1")
    with TestClient(create_app(make_session_factory(engine), MockProvider())) as c:
        root = new_conversation(c)["root_message_id"]
        reply(c, root, "ok")
        assert c.post(f"/api/messages/{root}/reply", json={"content": "x"}).status_code == 429
