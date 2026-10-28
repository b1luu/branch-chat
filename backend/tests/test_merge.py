"""D1 merge back: fork detection, the merge endpoint, and notes in later context."""

import json
from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.db import repository
from app.db.errors import ConflictError
from app.db.models import Note
from app.db.session import make_session_factory
from app.llm import DEFAULT_MAX_TOKENS, ChatMessage, LLMProvider, MockProvider
from app.main import create_app
from app.services.chat import NOTE_PREFIX
from app.services.merge import SUMMARY_SYSTEM_PROMPT, TURN_CHARS


class RecordingProvider(LLMProvider):
    """MockProvider that records every prompt (replies and summaries alike)."""

    name = "recording"

    def __init__(self) -> None:
        self.calls: list[tuple[list[ChatMessage], int]] = []

    async def stream(
        self, messages: list[ChatMessage], max_tokens: int = DEFAULT_MAX_TOKENS
    ) -> AsyncIterator[str]:
        self.calls.append((messages, max_tokens))
        async for chunk in MockProvider().stream(messages, max_tokens):
            yield chunk


class FailingProvider(LLMProvider):
    name = "failing"

    async def stream(
        self, messages: list[ChatMessage], max_tokens: int = DEFAULT_MAX_TOKENS
    ) -> AsyncIterator[str]:
        yield "partial"
        raise RuntimeError("model crashed")


class SilentProvider(LLMProvider):
    name = "silent"

    async def stream(
        self, messages: list[ChatMessage], max_tokens: int = DEFAULT_MAX_TOKENS
    ) -> AsyncIterator[str]:
        for chunk in ("", "  ", "\n"):
            yield chunk


@pytest.fixture
def recorder() -> RecordingProvider:
    return RecordingProvider()


@pytest.fixture
def rclient(engine: Engine, recorder: RecordingProvider) -> Iterator[TestClient]:
    with TestClient(create_app(make_session_factory(engine), recorder)) as c:
        yield c


def reply(client: TestClient, parent_id: str, content: str, quote: str | None = None) -> dict:
    """POST a reply and return the persisted assistant node (`done` event)."""
    body: dict = {"content": content} | ({"quote": quote} if quote else {})
    r = client.post(f"/api/messages/{parent_id}/reply", json=body)
    assert r.status_code == 200, r.text
    last = r.text.strip().split("\n\n")[-1]
    event, data = (line.split(": ", 1)[1] for line in last.splitlines())
    assert event == "done", r.text
    return json.loads(data)


def build_tree(client: TestClient) -> dict[str, str]:
    """root → u1/a1 ─┬→ main follow-up (a2)                (main thread)
                      └→ side q (b1) ─┬→ side more (b2)    (side branch)
                                      └→ nested (c1)       (nested side branch)
    Returns assistant ids by name (and `u_*` user ids for branch starts).
    """
    root = client.post("/api/conversations", json={}).json()["root_message_id"]
    a1 = reply(client, root, "What is a heap?")
    a2 = reply(client, a1["id"], "Main follow-up: and a stack?")
    b1 = reply(client, a1["id"], "Side q: why 'heap'?", quote="heap")
    b2 = reply(client, b1["id"], "Side more: any history?")
    c1 = reply(client, b1["id"], "Nested: other names?")
    return {
        "root": root,
        "a1": a1["id"],
        "a2": a2["id"],
        "u_side": b1["parent_id"],
        "b1": b1["id"],
        "b2": b2["id"],
        "u_nested": c1["parent_id"],
        "c1": c1["id"],
    }


# --- fork detection ------------------------------------------------------


def test_side_branch_start_finds_innermost_side_branch(client: TestClient, db: Session) -> None:
    ids = build_tree(client)

    path = repository.get_path(db, ids["b2"])
    assert path[repository.side_branch_start(db, path)].id == ids["u_side"]

    path = repository.get_path(db, ids["c1"])
    assert path[repository.side_branch_start(db, path)].id == ids["u_nested"]

    # The side branch's first node alone is still a side branch.
    path = repository.get_path(db, ids["u_side"])
    assert path[repository.side_branch_start(db, path)].id == ids["u_side"]


def test_side_branch_start_rejects_main_thread(client: TestClient, db: Session) -> None:
    ids = build_tree(client)
    for leaf in ("a2", "a1", "root"):
        with pytest.raises(ConflictError):
            repository.side_branch_start(db, repository.get_path(db, ids[leaf]))


# --- endpoint -------------------------------------------------------------


def test_merge_stores_note_on_fork_origin(
    rclient: TestClient, db: Session, recorder: RecordingProvider
) -> None:
    ids = build_tree(rclient)
    recorder.calls.clear()

    r = rclient.post(f"/api/branches/{ids['b2']}/merge")
    assert r.status_code == 201, r.text
    note = r.json()
    assert set(note) == {"id", "message_id", "source_leaf_id", "content", "created_at"}
    assert note["message_id"] == ids["a1"]
    assert note["source_leaf_id"] == ids["b2"]
    assert note["content"].startswith("Mock reply to")

    stored = db.scalars(select(Note)).all()
    assert [(n.message_id, n.source_leaf_id, n.content) for n in stored] == [
        (ids["a1"], ids["b2"], note["content"])
    ]

    # The summarizer saw the origin and the branch, not the main thread or the nested branch.
    [(prompt, max_tokens)] = recorder.calls
    assert prompt[0] == ChatMessage("system", SUMMARY_SYSTEM_PROMPT)
    assert [m.role for m in prompt] == ["system", "user"]
    transcript = prompt[1].content
    assert "Main conversation message the side discussion branched from:" in transcript
    assert 'User (about the excerpt "heap"): Side q:' in transcript
    assert "User: Side more: any history?" in transcript
    assert "Assistant: Mock reply to" in transcript
    assert "Main follow-up" not in transcript
    assert "Nested" not in transcript
    assert max_tokens == 300


def test_merge_nested_branch_goes_on_parent_branch(rclient: TestClient) -> None:
    ids = build_tree(rclient)
    r = rclient.post(f"/api/branches/{ids['c1']}/merge")
    assert r.status_code == 201
    assert r.json()["message_id"] == ids["b1"]


def test_merge_respects_merge_max_tokens(
    rclient: TestClient, recorder: RecordingProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = build_tree(rclient)
    monkeypatch.setenv("MERGE_MAX_TOKENS", "3")
    recorder.calls.clear()
    note = rclient.post(f"/api/branches/{ids['b1']}/merge").json()
    assert recorder.calls[0][1] == 3
    assert note["content"] == "Mock reply to"


def test_merge_clips_huge_turns(rclient: TestClient, recorder: RecordingProvider) -> None:
    ids = build_tree(rclient)
    long_leaf = reply(rclient, ids["b2"], "x" * (TURN_CHARS * 3))
    recorder.calls.clear()
    assert rclient.post(f"/api/branches/{long_leaf['id']}/merge").status_code == 201
    transcript = recorder.calls[0][0][1].content
    assert "x" * TURN_CHARS not in transcript
    assert "x" * (TURN_CHARS - 1) + "…" in transcript


def test_merge_main_thread_is_409(client: TestClient, db: Session) -> None:
    ids = build_tree(client)
    r = client.post(f"/api/branches/{ids['a2']}/merge")
    assert r.status_code == 409
    assert "main thread" in r.json()["detail"]
    assert db.scalars(select(Note)).all() == []


def test_merge_unknown_leaf_is_404(client: TestClient) -> None:
    r = client.post("/api/branches/nope/merge")
    assert r.status_code == 404
    assert r.json() == {"detail": "message nope not found"}


@pytest.mark.parametrize(
    ("provider", "detail"),
    [
        (FailingProvider(), "LLM provider error: model crashed"),
        (SilentProvider(), "LLM returned an empty summary"),
    ],
)
def test_merge_provider_failure_is_502_and_stores_nothing(
    engine: Engine, client: TestClient, db: Session, provider: LLMProvider, detail: str
) -> None:
    ids = build_tree(client)  # built with the mock; merge with the broken provider
    with TestClient(create_app(make_session_factory(engine), provider)) as broken:
        r = broken.post(f"/api/branches/{ids['b2']}/merge")
    assert r.status_code == 502
    assert r.json() == {"detail": detail}
    assert db.scalars(select(Note)).all() == []


# --- notes in context and in the path -----------------------------------


def note_messages(prompt: list[ChatMessage]) -> list[ChatMessage]:
    return [m for m in prompt if m.content.startswith(NOTE_PREFIX)]


def test_note_reaches_descendants_of_origin(
    rclient: TestClient, recorder: RecordingProvider
) -> None:
    ids = build_tree(rclient)
    note = rclient.post(f"/api/branches/{ids['b2']}/merge").json()

    recorder.calls.clear()
    reply(rclient, ids["a2"], "Back on main: and a queue?")
    prompt = recorder.calls[0][0]
    [note_msg] = note_messages(prompt)
    assert note_msg == ChatMessage("system", f"{NOTE_PREFIX}\n{note['content']}")
    # Inserted right after the origin (a1), before the main follow-up.
    origin_at = next(i for i, m in enumerate(prompt) if m.role == "assistant")
    assert prompt[origin_at + 1] == note_msg
    assert "Main follow-up" in prompt[origin_at + 2].content


def test_note_skipped_on_the_branch_it_summarizes(
    rclient: TestClient, recorder: RecordingProvider
) -> None:
    ids = build_tree(rclient)
    rclient.post(f"/api/branches/{ids['b2']}/merge")
    recorder.calls.clear()
    reply(rclient, ids["b2"], "Still on the side branch")
    assert note_messages(recorder.calls[0][0]) == []


def test_note_not_seen_above_the_origin(rclient: TestClient, recorder: RecordingProvider) -> None:
    ids = build_tree(rclient)
    rclient.post(f"/api/branches/{ids['b2']}/merge")
    recorder.calls.clear()
    reply(rclient, ids["root"], "A fresh first turn")  # sibling of u1: never passes a1
    assert note_messages(recorder.calls[0][0]) == []


def test_path_includes_notes_on_their_message(rclient: TestClient) -> None:
    ids = build_tree(rclient)
    first = rclient.post(f"/api/branches/{ids['b1']}/merge").json()
    second = rclient.post(f"/api/branches/{ids['b2']}/merge").json()

    path = rclient.get(f"/api/messages/{ids['a2']}/path").json()
    by_id = {m["id"]: m for m in path}
    assert [n["id"] for n in by_id[ids["a1"]]["notes"]] == [first["id"], second["id"]]
    assert all(m["notes"] == [] for m in path if m["id"] != ids["a1"])
