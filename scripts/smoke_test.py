#!/usr/bin/env python3
"""Smoke test for the docker-compose stack (stdlib only).

    python scripts/smoke_test.py                # stack already running on :8080
    python scripts/smoke_test.py --compose      # build + up, test, then down -v
    python scripts/smoke_test.py --base-url http://localhost:8000

Goes through the web container's nginx (so the /api proxy and SSE passthrough
are exercised too) and checks: health, create conversation, a streamed reply
persisted as user + assistant nodes, a sibling branch whose path excludes the
first branch, and the tree. Works with any LLM_PROVIDER: it checks structure,
never exact model text. Exit code 0 = pass.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

REPO = Path(__file__).resolve().parents[1]


class SmokeError(Exception):
    pass


class Transport(Protocol):
    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> tuple[int, bytes]: ...


@dataclass
class HttpTransport:
    base_url: str
    timeout: float = 120.0

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> tuple[int, bytes]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base_url.rstrip("/") + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data is not None else {},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()


@dataclass
class SseEvent:
    event: str
    data: Any = field(default=None)


def parse_sse(raw: bytes) -> list[SseEvent]:
    """Parse a complete SSE body into events (`data:` lines are JSON, joined by newlines)."""
    events: list[SseEvent] = []
    for block in raw.decode().replace("\r\n", "\n").split("\n\n"):
        name, data_lines = "message", []
        for line in block.split("\n"):
            if not line or line.startswith(":"):
                continue
            key, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if key == "event":
                name = value
            elif key == "data":
                data_lines.append(value)
        if data_lines:
            events.append(SseEvent(name, json.loads("\n".join(data_lines))))
    return events


def _json(
    transport: Transport,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    expect: int = 200,
) -> Any:
    status, raw = transport.request(method, path, body)
    if status != expect:
        raise SmokeError(f"{method} {path}: expected {expect}, got {status}: {raw[:300]!r}")
    return json.loads(raw)


def reply(
    transport: Transport, parent_id: str, content: str
) -> tuple[dict[str, Any], dict[str, Any], int]:
    """POST a reply; return (user node, assistant node, number of token events)."""
    path = f"/api/messages/{parent_id}/reply"
    status, raw = transport.request("POST", path, {"content": content})
    if status != 200:
        raise SmokeError(f"POST {path}: expected 200, got {status}: {raw[:300]!r}")
    events = parse_sse(raw)
    kinds = [e.event for e in events]
    if "error" in kinds:
        err = next(e for e in events if e.event == "error")
        raise SmokeError(f"reply stream error: {err.data}")
    if not kinds or kinds[0] != "user" or kinds[-1] != "done":
        raise SmokeError(f"unexpected SSE event sequence: {kinds}")
    tokens = [e for e in events if e.event == "token"]
    if not tokens:
        raise SmokeError("reply streamed no tokens")
    user, done = events[0].data, events[-1].data
    if done["parent_id"] != user["id"] or not done["content"]:
        raise SmokeError(f"assistant node malformed: {done}")
    return user, done, len(tokens)


def wait_healthy(transport: Transport, timeout: float, sleep: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    last = ""
    while True:
        try:
            status, raw = transport.request("GET", "/healthz")
            if status == 200 and json.loads(raw).get("status") == "ok":
                return
            last = f"HTTP {status}"
        except (OSError, ValueError) as exc:  # connection refused/reset while starting
            last = str(exc)
        if time.monotonic() >= deadline:
            raise SmokeError(f"/healthz not ok after {timeout:.0f}s ({last})")
        time.sleep(sleep)


def run_checks(transport: Transport, log: Any = print) -> None:
    conv = _json(transport, "POST", "/api/conversations", {"title": "smoke test"}, expect=201)
    root = conv["root_message_id"]
    log(f"ok  created conversation {conv['id']}")

    u1, a1, n1 = reply(transport, root, "Hello from the smoke test")
    log(f"ok  reply streamed {n1} token event(s)")

    path = _json(transport, "GET", f"/api/messages/{a1['id']}/path")
    if [m["id"] for m in path] != [root, u1["id"], a1["id"]]:
        raise SmokeError(f"path after first reply is wrong: {[m['id'] for m in path]}")
    log("ok  user + assistant nodes persisted on the path")

    u2, a2, _ = reply(transport, root, "A sibling branch")
    ids = [m["id"] for m in _json(transport, "GET", f"/api/messages/{a2['id']}/path")]
    if ids != [root, u2["id"], a2["id"]] or u1["id"] in ids:
        raise SmokeError(f"branch path leaked its sibling: {ids}")
    log("ok  sibling branch path excludes the first branch")

    tree = _json(transport, "GET", f"/api/conversations/{conv['id']}/tree")
    if len(tree["nodes"]) != 5:
        raise SmokeError(f"tree should have 5 nodes, has {len(tree['nodes'])}")
    log("ok  tree has both branches")


def compose(*args: str) -> None:
    subprocess.run(["docker", "compose", *args], cwd=REPO, check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument(
        "--compose",
        action="store_true",
        help="docker compose up --build first, and down -v afterwards",
    )
    parser.add_argument("--timeout", type=float, default=180.0, help="seconds to wait for health")
    args = parser.parse_args(argv)

    if args.compose:
        compose("up", "-d", "--build")
    try:
        transport = HttpTransport(args.base_url)
        wait_healthy(transport, args.timeout)
        print(f"ok  {args.base_url}/healthz")
        run_checks(transport)
    except SmokeError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        if args.compose:
            subprocess.run(
                ["docker", "compose", "logs", "--tail", "50", "api"],
                cwd=REPO,
                check=False,
            )
        return 1
    finally:
        if args.compose:
            compose("down", "-v")
    print("smoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
