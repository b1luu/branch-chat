"""Real-model tests against a local Ollama. Skipped automatically if unreachable.

Model output is random, so these assert structure (streaming, persistence,
what the prompt contains) and never exact wording.
"""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Message, Note, Role
from tests.integration.conftest import SYSTEM_PROMPT, CapturingOllamaProvider

pytestmark = pytest.mark.ollama

MERGE_ROUTE = "/api/branches/{leaf_id}/merge"


def parse_sse(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def new_root(client: TestClient) -> str:
    r = client.post("/api/conversations", json={"system_prompt": SYSTEM_PROMPT})
    assert r.status_code == 201
    return r.json()["root_message_id"]


def reply(
    client: TestClient, parent_id: str, content: str, quote: str | None = None
) -> tuple[list[tuple[str, dict]], dict, dict]:
    """POST a reply; return (events, user node, assistant node). Fails on `error`."""
    body: dict[str, str] = {"content": content}
    if quote is not None:
        body["quote"] = quote
    r = client.post(f"/api/messages/{parent_id}/reply", json=body)
    assert r.status_code == 200, r.text
    events = parse_sse(r.text)
    kinds = [k for k, _ in events]
    assert "error" not in kinds, events[-1]
    assert kinds[0] == "user" and kinds[-1] == "done", kinds
    return events, events[0][1], events[-1][1]


def prompt_text(payload: dict) -> str:
    return "\n".join(m["content"] for m in payload["messages"])


def test_reply_streams_multiple_nonempty_chunks(live_client: TestClient) -> None:
    events, _, done = reply(live_client, new_root(live_client), "Name three primary colors.")
    tokens = [d["text"] for k, d in events if k == "token"]
    assert len(tokens) > 1
    text = "".join(tokens)
    assert text.strip()
    assert done["content"] == text


def test_user_and_assistant_nodes_persisted(live_client: TestClient, db: Session) -> None:
    root = new_root(live_client)
    _, user, done = reply(live_client, root, "What is 2 + 2?")

    u = db.get(Message, user["id"])
    a = db.get(Message, done["id"])
    assert u is not None and u.role == Role.user and u.parent_id == root
    assert a is not None and a.role == Role.assistant and a.parent_id == u.id
    assert a.content.strip()


def test_branch_prompt_has_ancestors_not_siblings(
    live_client: TestClient, provider: CapturingOllamaProvider
) -> None:
    root = new_root(live_client)
    _, _, a1 = reply(live_client, root, "Tell me one fact about Saturn. ANCESTOR-MARK-4417")
    _, _, sib_asst = reply(live_client, a1["id"], "Now one fact about Mars. SIBLING-MARK-9083")
    reply(live_client, a1["id"], "Now one fact about Jupiter instead.")

    sent = provider.payloads[-1]
    text = prompt_text(sent)
    assert [m["role"] for m in sent["messages"]] == ["system", "user", "assistant", "user"]
    assert SYSTEM_PROMPT in text
    assert "ANCESTOR-MARK-4417" in text
    assert a1["content"] in text
    assert "SIBLING-MARK-9083" not in text
    assert sib_asst["content"] not in [m["content"] for m in sent["messages"]]


def test_quote_text_appears_in_prompt(
    live_client: TestClient, provider: CapturingOllamaProvider
) -> None:
    root = new_root(live_client)
    _, _, a1 = reply(live_client, root, "Explain recursion in one sentence.")
    quote = "QUOTE-MARK-5521 the function calls itself"
    reply(live_client, a1["id"], "What does this part mean?", quote=quote)

    last_user = provider.payloads[-1]["messages"][-1]
    assert last_user["role"] == "user"
    assert quote in last_user["content"]
    assert "What does this part mean?" in last_user["content"]


def test_merge_back_produces_nonempty_note(live_client: TestClient, db: Session) -> None:
    root = new_root(live_client)
    _, _, a1 = reply(live_client, root, "What is a binary tree?")
    reply(live_client, a1["id"], "Main thread follow-up: what is a heap?")
    _, _, side = reply(live_client, a1["id"], "Side question: what is tree height?")

    r = live_client.post(MERGE_ROUTE.format(leaf_id=side["id"]))
    assert r.status_code in (200, 201), r.text
    notes = db.scalars(select(Note).where(Note.message_id == a1["id"])).all()
    assert len(notes) == 1
    assert notes[0].content.strip()
