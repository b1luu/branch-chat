"""D4: Anthropic provider marks the shared ancestor prefix cacheable (payload-level tests)."""

import json
import logging
from itertools import pairwise
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy.orm import Session

from app.db.models import Conversation, Message, Note, Role
from app.llm import ChatMessage
from app.llm.anthropic import AnthropicProvider, inline_system_note
from app.services.budget import SUMMARY_PREFIX
from app.services.chat import NOTE_PREFIX, prompt_for
from tests.test_llm_http_providers import (
    ANTHROPIC_URL,
    CONTEXT,
    anthropic_events,
    collect,
    sse_body,
)

EPH = {"type": "ephemeral"}


def provider(**kw: Any) -> AnthropicProvider:
    return AnthropicProvider(api_key="k", **kw)


def breakpoints(payload: dict[str, Any]) -> list[str]:
    """Where cache_control markers sit: 'system' or 'messages[i]'."""
    found = []
    system = payload.get("system")
    if isinstance(system, list) and any("cache_control" in b for b in system):
        found.append("system")
    for i, turn in enumerate(payload["messages"]):
        content = turn["content"]
        if isinstance(content, list) and any("cache_control" in b for b in content):
            found.append(f"messages[{i}]")
    return found


def text_of(turn: dict[str, Any]) -> str:
    content = turn["content"]
    return content if isinstance(content, str) else "".join(b["text"] for b in content)


def test_payload_marks_system_ancestor_end_and_newest_turn() -> None:
    payload = provider().build_payload(CONTEXT, 64)
    assert payload == {
        "model": "claude-haiku-5-5",
        "max_tokens": 64,
        "system": [{"type": "text", "text": "Be brief.", "cache_control": EPH}],
        "messages": [
            {"role": "user", "content": "Hi"},
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Hello!", "cache_control": EPH}],
            },
            {
                "role": "user",
                "content": [{"type": "text", "text": "What is a CTE?", "cache_control": EPH}],
            },
        ],
        "stream": True,
    }


def test_never_more_than_four_breakpoints() -> None:
    long = [ChatMessage("system", "sys")] + [
        ChatMessage("user" if i % 2 == 0 else "assistant", f"t{i}") for i in range(41)
    ]
    marks = breakpoints(provider().build_payload(long, 10))
    assert marks == ["system", "messages[39]", "messages[40]"]


def test_single_turn_without_system_marks_only_that_turn() -> None:
    payload = provider().build_payload([ChatMessage("user", "hi")], 10)
    assert "system" not in payload
    assert breakpoints(payload) == ["messages[0]"]


def test_prompt_cache_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_PROMPT_CACHE", "false")
    payload = provider().build_payload(CONTEXT, 10)
    assert breakpoints(payload) == []
    assert payload["system"] == "Be brief."
    assert all(isinstance(t["content"], str) for t in payload["messages"])
    monkeypatch.setenv("ANTHROPIC_PROMPT_CACHE", "true")
    assert provider().prompt_cache is True
    assert provider(prompt_cache=False).prompt_cache is False


def test_sibling_branches_share_a_byte_identical_cached_prefix() -> None:
    """Two replies to the same assistant message: everything up to and including
    the breakpoint on that message is identical, so the second one is a cache hit."""
    base = CONTEXT[:3]  # system, user Hi, assistant Hello!
    a = provider().build_payload([*base, ChatMessage("user", "branch A")], 10)
    b = provider().build_payload([*base, ChatMessage("user", "branch B")], 10)
    assert a["system"] == b["system"]
    assert a["messages"][:2] == b["messages"][:2]
    assert "messages[1]" in breakpoints(a) and "messages[1]" in breakpoints(b)
    assert a["messages"][2] != b["messages"][2]


def test_next_turn_extends_the_previous_requests_prefix() -> None:
    """Reply N's last breakpoint (newest user turn) is a prefix of reply N+1."""
    first = provider().build_payload(CONTEXT, 10)
    second = provider().build_payload(
        [*CONTEXT, ChatMessage("assistant", "A recursive query."), ChatMessage("user", "More?")],
        10,
    )
    assert first["system"] == second["system"]
    assert [text_of(t) for t in first["messages"]] == [
        text_of(t) for t in second["messages"][: len(first["messages"])]
    ]
    assert breakpoints(second) == ["system", "messages[3]", "messages[4]"]


def test_merge_note_is_inlined_in_place_and_keeps_system_stable() -> None:
    """A note merged mid-path must not alter `system` (that would bust the whole cache)."""
    note = ChatMessage("system", f"{NOTE_PREFIX}\nThey settled on Postgres.")
    with_note = [*CONTEXT[:3], note, ChatMessage("user", "Next?")]
    without = [*CONTEXT[:3], ChatMessage("user", "Next?")]
    p1 = provider().build_payload(with_note, 10)
    p2 = provider().build_payload(without, 10)
    assert p1["system"] == p2["system"]
    assert p1["messages"][:2] == p2["messages"][:2]
    last = text_of(p1["messages"][-1])
    assert last == inline_system_note(note.content) + "\n\nNext?"


def test_budget_summary_stays_in_the_cached_system_prefix() -> None:
    summary = ChatMessage("system", f"{SUMMARY_PREFIX}\nEarlier: setup talk.")
    payload = provider().build_payload([CONTEXT[0], summary, *CONTEXT[1:]], 10)
    assert payload["system"] == [
        {"type": "text", "text": f"Be brief.\n\n{summary.content}", "cache_control": EPH}
    ]
    assert all("system_note" not in text_of(t) for t in payload["messages"])


def test_real_tree_path_with_note_builds_valid_alternating_payload(db: Session) -> None:
    conv = Conversation(title="t")
    db.add(conv)
    db.flush()
    root = Message(conversation_id=conv.id, role=Role.system, content="You are helpful.")
    db.add(root)
    db.flush()
    u1 = Message(conversation_id=conv.id, parent_id=root.id, role=Role.user, content="q1")
    db.add(u1)
    db.flush()
    a1 = Message(conversation_id=conv.id, parent_id=u1.id, role=Role.assistant, content="a1")
    db.add(a1)
    db.flush()
    side = Message(conversation_id=conv.id, parent_id=a1.id, role=Role.user, content="main")
    other = Message(conversation_id=conv.id, parent_id=a1.id, role=Role.user, content="side")
    db.add_all([side, other])
    db.flush()
    db.add(Note(message_id=a1.id, source_leaf_id=other.id, content="Side said X."))
    db.commit()

    payload = provider().build_payload(prompt_for(db, side.id), 10)
    roles = [t["role"] for t in payload["messages"]]
    assert roles[0] == "user"
    assert all(r1 != r2 for r1, r2 in pairwise(roles))
    assert "Side said X." in text_of(payload["messages"][-1])
    assert "Side said X." not in json.dumps(payload["system"])


@respx.mock
async def test_cached_payload_is_what_goes_over_the_wire(caplog: pytest.LogCaptureFixture) -> None:
    events = anthropic_events("ok")
    events[0] = {
        "type": "message_start",
        "message": {
            "id": "msg_1",
            "usage": {
                "input_tokens": 5,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 4096,
            },
        },
    }
    route = respx.post(ANTHROPIC_URL).mock(
        return_value=httpx.Response(200, content=sse_body(events, event_names=True))
    )
    with caplog.at_level(logging.INFO, logger="app.llm.anthropic"):
        assert await collect(provider(), CONTEXT, max_tokens=8) == ["ok"]
    sent = json.loads(route.calls.last.request.content)
    assert sent == provider().build_payload(CONTEXT, 8)
    assert breakpoints(sent) == ["system", "messages[1]", "messages[2]"]
    assert "cache_read=4096" in caplog.text
