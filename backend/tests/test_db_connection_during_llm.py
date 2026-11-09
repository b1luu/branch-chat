"""Q29: no database connection is held while waiting on the LLM.

A reply streams for seconds to minutes (a cold local model, a long answer).
Before Q29, `chat.stream_reply` opened its session before building the prompt
and kept it until the assistant node was saved, so the connection it used for
the path query stayed checked out (on Postgres: "idle in transaction") for the
whole generation, including the D2 ancestor summary call. The pool is small
(SQLAlchemy's default is 5 + 10 overflow), so a handful of concurrent replies
starved every other request, which then waited `pool_timeout` (30 s) and failed.
Merge back had the same shape: the request session kept its read transaction
open during the summarizer call.

These tests use a one-connection pool and a provider that checks, while it is
generating, that the pool is free and that another request could use it.
"""

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool

from app.db.base import Base
from app.db.models import Message, Note, Role
from app.db.session import make_session_factory
from app.llm import DEFAULT_MAX_TOKENS, ChatMessage, LLMProvider, MockProvider
from app.main import create_app
from app.services import budget
from tests.test_merge import build_tree
from tests.test_reply_sse import done, parse_sse, reply


@pytest.fixture
def small_pool(tmp_path: Path) -> Iterator[Engine]:
    """File SQLite behind a 1-connection pool that gives up after 0.2 s."""
    eng = create_engine(
        f"sqlite:///{tmp_path / 'pool.db'}",
        connect_args={"check_same_thread": False},
        poolclass=QueuePool,
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.2,
    )
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


class PoolProbeProvider(LLMProvider):
    """MockProvider that, before generating, records whether the pool is free
    and whether another session can run a query (as a concurrent request would)."""

    name = "pool-probe"

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.checked_out: list[int] = []
        self.other_request_ok: list[bool] = []

    async def stream(
        self, messages: list[ChatMessage], max_tokens: int = DEFAULT_MAX_TOKENS
    ) -> AsyncIterator[str]:
        pool = self.engine.pool
        assert isinstance(pool, QueuePool)
        self.checked_out.append(pool.checkedout())
        try:
            with Session(self.engine) as other:
                other.execute(text("SELECT 1"))
            self.other_request_ok.append(True)
        except PoolTimeout:  # the pool is exhausted
            self.other_request_ok.append(False)
        async for chunk in MockProvider().stream(messages, max_tokens):
            yield chunk


def probe_client(engine: Engine, provider: LLMProvider) -> TestClient:
    return TestClient(create_app(make_session_factory(engine), provider))


def test_reply_holds_no_connection_while_streaming(small_pool: Engine) -> None:
    probe = PoolProbeProvider(small_pool)
    with probe_client(small_pool, probe) as c:
        root = c.post("/api/conversations", json={}).json()["root_message_id"]
        a1 = done(reply(c, root, "first"))
        reply(c, a1["id"], "second")
    assert probe.checked_out == [0, 0]
    assert probe.other_request_ok == [True, True]
    with Session(small_pool) as db:
        assert len(db.scalars(select(Message).where(Message.role == Role.assistant)).all()) == 2


def test_retry_reusing_a_failed_turn_holds_no_connection(small_pool: Engine) -> None:
    """The orphan-reuse path (create_user_node returns an existing row without a
    commit) must not leave the request session's read transaction open either."""
    probe = PoolProbeProvider(small_pool)
    with probe_client(small_pool, probe) as c:
        root = c.post("/api/conversations", json={}).json()["root_message_id"]
    with Session(small_pool, expire_on_commit=False) as db:
        orphan = Message(
            conversation_id=db.get(Message, root).conversation_id,  # type: ignore[union-attr]
            parent_id=root,
            role=Role.user,
            content="retry me",
        )
        db.add(orphan)
        db.commit()
    with probe_client(small_pool, probe) as c:
        a = done(reply(c, root, "retry me"))
    assert a["parent_id"] == orphan.id
    assert probe.checked_out == [0]
    assert probe.other_request_ok == [True]


def test_ancestor_summary_call_holds_no_connection(
    small_pool: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D2: with a tiny budget the reply first calls the LLM to summarize old turns."""
    budget.clear_summary_cache()
    monkeypatch.setenv("CONTEXT_TOKEN_BUDGET", "60")
    monkeypatch.setenv("CONTEXT_SUMMARY_MAX_TOKENS", "10")
    probe = PoolProbeProvider(small_pool)
    with probe_client(small_pool, probe) as c:
        root = c.post("/api/conversations", json={}).json()["root_message_id"]
        leaf = root
        for i in range(4):
            leaf = done(reply(c, leaf, f"turn {i} " + "words " * 20))["id"]
    # More provider calls than replies: the extra ones are summaries.
    assert len(probe.checked_out) > 4
    assert set(probe.checked_out) == {0}
    assert all(probe.other_request_ok)
    budget.clear_summary_cache()


def test_merge_holds_no_connection_while_summarizing(small_pool: Engine) -> None:
    with probe_client(small_pool, MockProvider()) as c:
        ids = build_tree(c)
    probe = PoolProbeProvider(small_pool)
    with probe_client(small_pool, probe) as c:
        r = c.post(f"/api/branches/{ids['b2']}/merge")
    assert r.status_code == 201, r.text
    assert r.json()["message_id"] == ids["a1"]  # b2's branch forked off a1
    assert probe.checked_out == [0]
    assert probe.other_request_ok == [True]
    with Session(small_pool) as db:
        assert [n.source_leaf_id for n in db.scalars(select(Note))] == [ids["b2"]]


class FlakyFactory:
    """Session factory whose `fail_on`-th session can't connect."""

    def __init__(self, inner: sessionmaker[Session], fail_on: int) -> None:
        self.inner = inner
        self.fail_on = fail_on
        self.calls = 0

    def __call__(self) -> Session:
        self.calls += 1
        if self.calls == self.fail_on:
            raise OperationalError("SELECT 1", {}, Exception("database is restarting"))
        return self.inner()


def test_prompt_load_failure_ends_stream_with_error(engine: Engine) -> None:
    """Loading the path now happens in its own short session; if the database
    is unreachable at that moment the client gets an `error` event, not a
    stream that stops right after `user`."""
    factory = make_session_factory(engine)
    with TestClient(create_app(factory, MockProvider())) as c:
        root = c.post("/api/conversations", json={}).json()["root_message_id"]
        # The request session is #1; the reply body's path load is #2.
        c.app.state.session_factory = FlakyFactory(factory, fail_on=2)  # type: ignore[attr-defined]
        r = c.post(f"/api/messages/{root}/reply", json={"content": "hi"})
    assert r.status_code == 200
    events = parse_sse(r.text)
    assert [e for e, _ in events] == ["user", "error"]
    assert "retry" in events[1][1]["detail"].lower()
