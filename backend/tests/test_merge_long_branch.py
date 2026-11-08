"""Q27: merging a long side branch keeps the summarizer prompt within the context budget."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.db.session import make_session_factory
from app.main import create_app
from app.services import merge
from app.services.merge import MIN_TRANSCRIPT_CHARS, TURN_CHARS, _fit_turns, transcript_chars_for
from tests.test_merge import RecordingProvider, build_tree, reply


@pytest.fixture
def recorder() -> RecordingProvider:
    return RecordingProvider()


@pytest.fixture
def rclient(engine: Engine, recorder: RecordingProvider) -> Iterator[TestClient]:
    with TestClient(create_app(make_session_factory(engine), recorder)) as c:
        yield c


def grow(client: TestClient, leaf_id: str, turns: int, size: int) -> str:
    """Extend a branch by `turns` user/assistant pairs; each user turn is `size` chars."""
    for i in range(turns):
        leaf_id = reply(client, leaf_id, f"turn{i:03d} " + "y" * size)["id"]
    return leaf_id


def test_long_branch_transcript_fits_budget(
    rclient: TestClient, recorder: RecordingProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = build_tree(rclient)
    leaf = grow(rclient, ids["b2"], 40, 1_000)  # ~40k chars of side discussion
    monkeypatch.setenv("CONTEXT_TOKEN_BUDGET", "3000")  # ~12k chars total
    recorder.calls.clear()
    r = rclient.post(f"/api/branches/{leaf}/merge")
    assert r.status_code == 201, r.text
    prompt, max_tokens = recorder.calls[0]
    transcript = prompt[1].content
    assert len(transcript) <= transcript_chars_for(3000, max_tokens) + 200
    # what was asked (the side question) and what was concluded (the latest turns) survive
    assert "Side q: why 'heap'?" in transcript
    assert "turn039" in transcript
    assert "earlier turns of the side discussion omitted" in transcript
    assert "turn000" not in transcript
    assert transcript.endswith("Summarize the side discussion.")


def test_short_branch_transcript_unchanged(
    rclient: TestClient, recorder: RecordingProvider
) -> None:
    ids = build_tree(rclient)
    recorder.calls.clear()
    assert rclient.post(f"/api/branches/{ids['b2']}/merge").status_code == 201
    transcript = recorder.calls[0][0][1].content
    assert "omitted" not in transcript
    assert "Side q: why 'heap'?" in transcript and "Side more: any history?" in transcript


def test_default_budget_bounds_a_huge_branch(
    rclient: TestClient, recorder: RecordingProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CONTEXT_TOKEN_BUDGET", raising=False)
    ids = build_tree(rclient)
    leaf = grow(rclient, ids["b2"], 70, TURN_CHARS)  # ~280k chars > 60k-token default
    recorder.calls.clear()
    assert rclient.post(f"/api/branches/{leaf}/merge").status_code == 201
    prompt, max_tokens = recorder.calls[0]
    cap = transcript_chars_for(merge.budget.DEFAULT_CONTEXT_TOKEN_BUDGET, max_tokens)
    assert len(prompt[1].content) <= cap + 200
    assert "turn069" in prompt[1].content


def test_fit_turns_keeps_first_last_and_most_recent() -> None:
    turns = [f"t{i}" + "x" * 98 for i in range(10)]  # 100 chars each
    out = _fit_turns(turns, 500)
    assert out[0] == turns[0] and out[-1] == turns[-1]
    assert "omitted" in out[1]
    kept = out[2:]
    assert kept == turns[-len(kept) :]  # a contiguous recent tail
    n = len(turns) - 1 - len(kept)
    assert f"{n} earlier turns" in out[1]
    assert sum(len(t) + 1 for t in out) <= 500


def test_fit_turns_never_drops_first_or_last() -> None:
    turns = ["q" * 300, "m" * 300, "a" * 300]
    assert _fit_turns(turns, 100) == [
        turns[0],
        "[… 1 earlier turns of the side discussion omitted …]",
        turns[2],
    ]
    assert _fit_turns(turns[:2], 10) == turns[:2]
    assert _fit_turns(turns, 10_000) == turns


def test_transcript_cap_has_a_floor() -> None:
    assert transcript_chars_for(10, 300) == MIN_TRANSCRIPT_CHARS
    assert transcript_chars_for(60_000, 300) > 200_000
