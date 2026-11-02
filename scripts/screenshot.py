#!/usr/bin/env python3
"""Regenerate the README screenshot (`docs/screenshot.png`). Dev-only, no network.

    cd frontend && npm run build && cd ..
    python scripts/screenshot.py [--dark] [--out docs/screenshot.png]

Needs the backend installed (see README: `pip install -r requirements-dev.lock`
in `backend/`) and Playwright for Python with Chromium. Serves the built frontend and the API from one in-process
FastAPI app on a throwaway SQLite DB, with a scripted mock provider (canned
Markdown replies, so the shot shows real formatting rather than mock echo text).
It then builds a branched conversation through the real API: two main-thread
turns, a quote branch ("Ask about this") off the first answer, and a merge back,
and screenshots the side branch, where the quote, the merged note on its parent,
the breadcrumb and the marked side branch in the tree are all visible.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import tempfile
import threading
import time
from collections.abc import AsyncIterator
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

import httpx
import uvicorn
from fastapi.staticfiles import StaticFiles

from app.api.ratelimit import RateLimitConfig
from app.db.base import Base
from app.db.session import make_engine, make_session_factory
from app.llm.base import DEFAULT_MAX_TOKENS, ChatMessage, LLMProvider
from app.main import create_app

# Replies in the order the script asks for them (3 replies, then the merge summary).
SCRIPT = [
    (
        "A **recursive CTE** is a `WITH RECURSIVE` query that refers to itself. It has two parts:\n\n"
        "1. The **anchor member**: the starting rows (here, the message you clicked).\n"
        "2. The **recursive member**: joins each new row to its parent, one level per step.\n\n"
        "```sql\nWITH RECURSIVE path AS (\n  SELECT * FROM messages WHERE id = :leaf\n"
        "  UNION ALL\n  SELECT m.* FROM messages m JOIN path p ON m.id = p.parent_id\n)\n"
        "SELECT * FROM path;\n```"
    ),
    (
        "It stops when the recursive member returns **no new rows**: once it reaches the root, "
        "`parent_id` is `NULL` and the join finds nothing."
    ),
    (
        "The anchor member runs **once**, before any recursion. Think of it as the seed:\n\n"
        "- it picks the leaf message by `id`\n- every later step walks one `parent_id` up from the "
        "previous step's rows\n\nWithout it, the recursion would have nothing to start from."
    ),
    (
        "The anchor member is the one-time seed row (the selected leaf); "
        "the recursive member then walks up `parent_id` until the root."
    ),
]


class ScriptedProvider(LLMProvider):
    """Mock provider that streams the next canned reply, word by word."""

    name = "mock"

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)

    async def stream(
        self, messages: list[ChatMessage], max_tokens: int = DEFAULT_MAX_TOKENS
    ) -> AsyncIterator[str]:
        text = self._replies.pop(0) if self._replies else "(no more scripted replies)"
        for i, word in enumerate(text.split(" ")):
            yield word if i == 0 else " " + word


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def reply(client: httpx.Client, parent: str, content: str, quote: str | None = None) -> str:
    """POST a reply, drain the SSE stream, return the persisted assistant id."""
    body: dict[str, str] = {"content": content}
    if quote:
        body["quote"] = quote
    done: str | None = None
    with client.stream("POST", f"/api/messages/{parent}/reply", json=body) as r:
        r.raise_for_status()
        event = ""
        for line in r.iter_lines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and event == "done":
                done = json.loads(line.split(":", 1)[1])["id"]
            elif line.startswith("data:") and event == "error":
                raise RuntimeError(line)
    if done is None:
        raise RuntimeError("stream ended without a done event")
    return done


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=REPO / "docs" / "screenshot.png")
    parser.add_argument("--dark", action="store_true", help="dark color scheme")
    args = parser.parse_args()

    dist = REPO / "frontend" / "dist"
    if not (dist / "index.html").exists():
        print("frontend/dist missing: run `npm run build` in frontend/ first", file=sys.stderr)
        return 1
    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory() as tmp:
        engine = make_engine(f"sqlite:///{tmp}/shot.db")
        Base.metadata.create_all(engine)
        app = create_app(
            session_factory=make_session_factory(engine),
            llm_provider=ScriptedProvider(SCRIPT),
            rate_limit=RateLimitConfig(enabled=False),
        )
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
        port = free_port()
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{port}"
        while not server.started:
            time.sleep(0.05)

        with httpx.Client(base_url=base, timeout=10) as client:
            conv = client.post("/api/conversations", json={"title": "Recursive CTEs"}).json()
            a1 = reply(client, conv["root_message_id"], "What's a recursive CTE?")
            reply(client, a1, "How does it know when to stop?")
            side = reply(client, a1, "Why does it only run once?", quote="anchor member")
            client.post(f"/api/branches/{side}/merge").raise_for_status()

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(
                viewport={"width": 1280, "height": 860},
                color_scheme="dark" if args.dark else "light",
                device_scale_factor=2,
            )
            page.goto(base)
            page.get_by_role("button", name="Recursive CTEs").click()
            # Opening resumes at the newest node: the quote branch's answer.
            page.get_by_role("button", name="Back to main thread").wait_for()
            page.get_by_text("Merged from a side branch").wait_for()
            args.out.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(args.out))
            browser.close()
        server.should_exit = True
        thread.join(timeout=5)
        engine.dispose()
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
