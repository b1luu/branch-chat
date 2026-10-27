from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.schemas import DEFAULT_SYSTEM_PROMPT, PREVIEW_CHARS
from app.db.models import Message, Role


def _create(client: TestClient, **body: str) -> dict:
    response = client.post("/api/conversations", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _add(db: Session, conv_id: str, parent_id: str, role: Role, content: str) -> Message:
    msg = Message(conversation_id=conv_id, parent_id=parent_id, role=role, content=content)
    db.add(msg)
    db.commit()
    return msg


# --- create / list -----------------------------------------------------------


def test_create_conversation_defaults(client: TestClient, db: Session) -> None:
    conv = _create(client)
    assert conv["title"] == "New conversation"
    assert conv["root_message_id"]
    root = db.get(Message, conv["root_message_id"])
    assert root is not None
    assert root.conversation_id == conv["id"]
    assert root.parent_id is None
    assert root.role == Role.system
    assert root.content == DEFAULT_SYSTEM_PROMPT


def test_create_conversation_with_title_and_system_prompt(client: TestClient, db: Session) -> None:
    conv = _create(client, title="Rust lifetimes", system_prompt="Be terse.")
    assert conv["title"] == "Rust lifetimes"
    root = db.get(Message, conv["root_message_id"])
    assert root is not None and root.content == "Be terse."


def test_create_conversation_rejects_empty_title(client: TestClient) -> None:
    response = client.post("/api/conversations", json={"title": ""})
    assert response.status_code == 422


def test_list_conversations_empty(client: TestClient) -> None:
    response = client.get("/api/conversations")
    assert response.status_code == 200
    assert response.json() == []


def test_list_conversations_newest_first(client: TestClient) -> None:
    first = _create(client, title="first")
    second = _create(client, title="second")
    listed = client.get("/api/conversations").json()
    assert [c["id"] for c in listed] == [second["id"], first["id"]]
    assert listed[1]["root_message_id"] == first["root_message_id"]


# --- tree --------------------------------------------------------------------


def test_tree_new_conversation_has_only_root(client: TestClient) -> None:
    conv = _create(client)
    tree = client.get(f"/api/conversations/{conv['id']}/tree").json()
    assert tree["conversation_id"] == conv["id"]
    assert [(n["id"], n["parent_id"], n["role"]) for n in tree["nodes"]] == [
        (conv["root_message_id"], None, "system")
    ]


def test_tree_returns_all_branches_parents_first(client: TestClient, db: Session) -> None:
    conv = _create(client)
    root = conv["root_message_id"]
    q = _add(db, conv["id"], root, Role.user, "question")
    a = _add(db, conv["id"], q.id, Role.assistant, "answer")
    main = _add(db, conv["id"], a.id, Role.user, "main follow-up")
    side = _add(db, conv["id"], a.id, Role.user, "side question")

    nodes = client.get(f"/api/conversations/{conv['id']}/tree").json()["nodes"]
    ids = [n["id"] for n in nodes]
    assert set(ids) == {root, q.id, a.id, main.id, side.id}
    # Every parent appears before its children, so the client can build in one pass.
    for n in nodes:
        if n["parent_id"] is not None:
            assert ids.index(n["parent_id"]) < ids.index(n["id"])
    by_id = {n["id"]: n for n in nodes}
    assert by_id[main.id]["parent_id"] == a.id
    assert by_id[side.id]["parent_id"] == a.id


def test_tree_does_not_include_other_conversations(client: TestClient, db: Session) -> None:
    a = _create(client)
    b = _create(client)
    _add(db, b["id"], b["root_message_id"], Role.user, "in b")
    nodes = client.get(f"/api/conversations/{a['id']}/tree").json()["nodes"]
    assert [n["id"] for n in nodes] == [a["root_message_id"]]


def test_tree_preview_is_single_line_and_truncated(client: TestClient, db: Session) -> None:
    conv = _create(client)
    long = _add(db, conv["id"], conv["root_message_id"], Role.user, "word\n" * 100)
    nodes = client.get(f"/api/conversations/{conv['id']}/tree").json()["nodes"]
    preview = next(n["preview"] for n in nodes if n["id"] == long.id)
    assert "\n" not in preview
    assert len(preview) == PREVIEW_CHARS
    assert preview.endswith("…")
    assert "content" not in nodes[0]


def test_tree_missing_conversation_404(client: TestClient) -> None:
    response = client.get("/api/conversations/nope/tree")
    assert response.status_code == 404
    assert response.json() == {"detail": "conversation nope not found"}


# --- path --------------------------------------------------------------------


def test_path_returns_root_to_node_without_siblings(client: TestClient, db: Session) -> None:
    conv = _create(client)
    root = conv["root_message_id"]
    q = _add(db, conv["id"], root, Role.user, "question")
    a = _add(db, conv["id"], q.id, Role.assistant, "answer")
    _add(db, conv["id"], a.id, Role.user, "main follow-up")
    side = _add(db, conv["id"], a.id, Role.user, "side question")

    response = client.get(f"/api/messages/{side.id}/path")
    assert response.status_code == 200
    path = response.json()
    assert [m["id"] for m in path] == [root, q.id, a.id, side.id]
    assert [m["content"] for m in path][1:] == ["question", "answer", "side question"]
    assert set(path[0]) == {
        "id",
        "conversation_id",
        "parent_id",
        "role",
        "content",
        "quote",
        "created_at",
    }


def test_path_includes_quote(client: TestClient, db: Session) -> None:
    conv = _create(client)
    msg = Message(
        conversation_id=conv["id"],
        parent_id=conv["root_message_id"],
        role=Role.user,
        content="what does this mean?",
        quote="borrow checker",
    )
    db.add(msg)
    db.commit()
    path = client.get(f"/api/messages/{msg.id}/path").json()
    assert path[-1]["quote"] == "borrow checker"


def test_path_missing_message_404(client: TestClient) -> None:
    response = client.get("/api/messages/nope/path")
    assert response.status_code == 404
    assert response.json() == {"detail": "message nope not found"}
