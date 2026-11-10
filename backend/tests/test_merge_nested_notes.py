"""Q31: merging a side branch includes notes merged into it from nested branches.

Before the fix the summarizer transcript was only the branch's own turns, so a
nested side discussion that had already been merged into the branch (its note
shown inline there, and part of the branch's later context) was missing from
the branch's own merge note: its conclusion never made it back to the main
thread.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.db.models import Message, Note, Role
from app.db.session import make_session_factory
from app.main import create_app
from app.services.merge import NESTED_NOTE, OMITTED_TURNS, build_summary_prompt
from tests.test_merge import RecordingProvider, build_tree, reply


@pytest.fixture
def recorder() -> RecordingProvider:
    return RecordingProvider()


@pytest.fixture
def rclient(engine: Engine, recorder: RecordingProvider) -> Iterator[TestClient]:
    with TestClient(create_app(make_session_factory(engine), recorder)) as c:
        yield c


def last_transcript(recorder: RecordingProvider) -> str:
    messages, _ = recorder.calls[-1]
    return messages[-1].content


def test_merge_includes_note_from_merged_nested_branch(
    rclient: TestClient, recorder: RecordingProvider
) -> None:
    ids = build_tree(rclient)
    nested = rclient.post(f"/api/branches/{ids['c1']}/merge")
    assert nested.status_code == 201
    nested_note = nested.json()
    assert nested_note["message_id"] == ids["b1"]

    r = rclient.post(f"/api/branches/{ids['b2']}/merge")
    assert r.status_code == 201
    transcript = last_transcript(recorder)
    expected = NESTED_NOTE.format(text=nested_note["content"])
    assert expected in transcript
    # Placed right after the message it is pinned to (b1), before the next turn.
    assert transcript.index(expected) < transcript.index("Side more: any history?")
    # The nested branch's own turns are still not replayed.
    assert "Nested: other names?" not in transcript


def test_merge_without_nested_notes_is_unchanged(
    rclient: TestClient, recorder: RecordingProvider
) -> None:
    ids = build_tree(rclient)
    rclient.post(f"/api/branches/{ids['b2']}/merge")
    assert "Note (merged back" not in last_transcript(recorder)


def test_notes_on_the_fork_origin_are_not_part_of_the_branch(
    rclient: TestClient, recorder: RecordingProvider
) -> None:
    """A sibling side branch merged onto the origin is not this branch's discussion."""
    ids = build_tree(rclient)
    other_id = reply(rclient, ids["a1"], "Other side q")["id"]
    first = rclient.post(f"/api/branches/{other_id}/merge").json()
    assert first["message_id"] == ids["a1"]

    rclient.post(f"/api/branches/{ids['b2']}/merge")
    assert first["content"] not in last_transcript(recorder).split("Side discussion:", 1)[1]


def _msg(id_: str, role: Role, content: str, parent: str | None) -> Message:
    return Message(id=id_, conversation_id="c", parent_id=parent, role=role, content=content)


def test_note_from_a_leaf_on_the_branch_is_skipped() -> None:
    origin = _msg("o", Role.assistant, "origin", None)
    u = _msg("u", Role.user, "side q", "o")
    a = _msg("a", Role.assistant, "side a", "u")
    own = Note(id="n1", message_id="u", source_leaf_id="a", content="SELF")
    nested = Note(id="n2", message_id="u", source_leaf_id="elsewhere", content="NESTED")
    prompt = build_summary_prompt(origin, [u, a], notes={"u": [own, nested]})
    text = prompt[-1].content
    assert "SELF" not in text
    assert NESTED_NOTE.format(text="NESTED") in text


def test_nested_note_is_kept_or_dropped_with_its_turn() -> None:
    """Trimming a long branch counts a turn and its notes as one entry."""
    origin = _msg("o", Role.assistant, "origin", None)
    branch: list[Message] = []
    parent = "o"
    for i in range(40):
        role = Role.user if i % 2 == 0 else Role.assistant
        m = _msg(f"m{i}", role, f"turn {i} " + "x" * 200, parent)
        branch.append(m)
        parent = m.id
    notes = {
        "m3": [Note(id="early", message_id="m3", source_leaf_id="z1", content="EARLY")],
        "m37": [Note(id="late", message_id="m37", source_leaf_id="z2", content="LATE")],
    }
    text = build_summary_prompt(origin, branch, max_chars=3_000, notes=notes)[-1].content
    omitted = OMITTED_TURNS.split("{n}")[0]
    assert omitted in text
    assert "LATE" in text  # recent turn kept, with its note
    assert "turn 3 " not in text and "EARLY" not in text  # dropped together
    n = int(text.split(omitted, 1)[1].split()[0])
    shown = sum(1 for i in range(40) if f"turn {i} " in text)
    assert n + shown == 40
