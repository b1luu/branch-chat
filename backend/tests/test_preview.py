"""Tree previews strip Markdown from assistant replies (U2 follow-up)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.schemas import PREVIEW_CHARS
from app.db.models import Message, Role
from app.services.preview import make_preview, markdown_to_text


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("**Bold** and *italic* and __strong__ and _em_", "Bold and italic and strong and em"),
        ("Use `pip install` here", "Use pip install here"),
        ("``code with ` tick``", "code with ` tick"),
        ("# Title\n## Sub", "Title\nSub"),
        ("- one\n* two\n+ three\n1. four\n2) five", "one\ntwo\nthree\nfour\nfive"),
        ("- [ ] todo\n- [x] done", "todo\ndone"),
        ("> quoted\n>> nested", "quoted\nnested"),
        ("See [the docs](https://example.com) now", "See the docs now"),
        ("![a diagram](x.png)", "a diagram"),
        ("~~old~~ new", "old new"),
        ("above\n\n---\n\nbelow", "above\n\n\n\nbelow"),
    ],
)
def test_markdown_to_text_strips_syntax(source: str, expected: str) -> None:
    assert markdown_to_text(source) == expected


def test_fenced_code_keeps_code_drops_fences() -> None:
    text = markdown_to_text("Try:\n```python\nprint(1)\n```\ndone")
    assert "```" not in text
    assert "python" not in text
    assert "print(1)" in text


def test_inline_code_contents_are_not_treated_as_emphasis() -> None:
    assert markdown_to_text("`a*b*c` and `__init__`") == "a*b*c and __init__"


def test_snake_case_and_lone_symbols_survive() -> None:
    text = "call my_func_name with 2 * 3 * 4 and a_b"
    assert markdown_to_text(text) == text


def test_table_separator_dropped() -> None:
    text = markdown_to_text("| a | b |\n|---|:---:|\n| 1 | 2 |")
    assert "---" not in text
    assert "a" in text and "1" in text


def test_unclosed_emphasis_is_left_alone() -> None:
    # A reply cut off mid-stream (failed reply) may end with an open marker.
    assert markdown_to_text("starts **bold but never") == "starts **bold but never"


def test_strip_happens_before_truncation() -> None:
    content = "**" + "x" * (PREVIEW_CHARS - 2) + "**"
    preview = make_preview(content, Role.assistant, PREVIEW_CHARS)
    # Visible text is PREVIEW_CHARS - 2 chars: fits, no ellipsis, no markers.
    assert preview == "x" * (PREVIEW_CHARS - 2)


def test_user_turns_are_not_stripped() -> None:
    assert make_preview("what does **kwargs do?", Role.user, PREVIEW_CHARS) == (
        "what does **kwargs do?"
    )


def test_tree_endpoint_returns_plain_assistant_preview(client: TestClient, db: Session) -> None:
    conv = client.post("/api/conversations", json={"title": "t"}).json()
    user = Message(
        conversation_id=conv["id"],
        parent_id=conv["root_message_id"],
        role=Role.user,
        content="explain `**kwargs`",
    )
    db.add(user)
    db.flush()
    reply = Message(
        conversation_id=conv["id"],
        parent_id=user.id,
        role=Role.assistant,
        content="## Short answer\n\n- `**kwargs` collects **keyword** arguments\n- see [docs](https://x)",
    )
    db.add(reply)
    db.commit()

    nodes = client.get(f"/api/conversations/{conv['id']}/tree").json()["nodes"]
    by_id = {n["id"]: n["preview"] for n in nodes}
    assert by_id[reply.id] == "Short answer **kwargs collects keyword arguments see docs"
    assert by_id[user.id] == "explain `**kwargs`"
