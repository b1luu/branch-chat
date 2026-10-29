"""S1: scripts/smoke_test.py, run against the in-process app instead of docker.

The real target is the compose stack (`python scripts/smoke_test.py --compose`);
here a TestClient stands in for HTTP so the checks themselves are tested.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi.testclient import TestClient

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "smoke_test.py"


@pytest.fixture(scope="module")
def smoke() -> ModuleType:
    spec = importlib.util.spec_from_file_location("smoke_test", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["smoke_test"] = mod  # dataclasses need the module registered
    spec.loader.exec_module(mod)
    return mod


class ClientTransport:
    def __init__(self, client: TestClient) -> None:
        self.client = client

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> tuple[int, bytes]:
        resp = self.client.request(method, path, json=body)
        return resp.status_code, resp.content


class FakeTransport:
    """Returns canned responses keyed by `METHOD path`."""

    def __init__(self, routes: dict[str, tuple[int, bytes]]) -> None:
        self.routes = routes

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> tuple[int, bytes]:
        return self.routes[f"{method} {path}"]


def test_run_checks_pass_against_the_app(smoke: ModuleType, client: TestClient) -> None:
    lines: list[str] = []
    smoke.run_checks(ClientTransport(client), log=lines.append)
    assert len(lines) == 5 and all(line.startswith("ok") for line in lines)


def test_wait_healthy_ok(smoke: ModuleType, client: TestClient) -> None:
    smoke.wait_healthy(ClientTransport(client), timeout=1)


def test_wait_healthy_times_out(smoke: ModuleType) -> None:
    t = FakeTransport({"GET /healthz": (503, b"{}")})
    with pytest.raises(smoke.SmokeError, match="HTTP 503"):
        smoke.wait_healthy(t, timeout=0, sleep=0)


def test_parse_sse_handles_crlf_comments_and_multiline(smoke: ModuleType) -> None:
    raw = b': keepalive\r\n\r\nevent: token\r\ndata: {"text":\r\ndata: "hi"}\r\n\r\ndata: 1\n\n'
    events = smoke.parse_sse(raw)
    assert [(e.event, e.data) for e in events] == [("token", {"text": "hi"}), ("message", 1)]


def _sse(*events: tuple[str, str]) -> bytes:
    return "".join(f"event: {name}\ndata: {data}\n\n" for name, data in events).encode()


def test_reply_reports_stream_error(smoke: ModuleType) -> None:
    body = _sse(("user", '{"id": "u"}'), ("error", '{"detail": "provider down"}'))
    t = FakeTransport({"POST /api/messages/r/reply": (200, body)})
    with pytest.raises(smoke.SmokeError, match="provider down"):
        smoke.reply(t, "r", "hi")


def test_reply_requires_tokens(smoke: ModuleType) -> None:
    body = _sse(("user", '{"id": "u"}'), ("done", '{"id": "a", "parent_id": "u", "content": "x"}'))
    t = FakeTransport({"POST /api/messages/r/reply": (200, body)})
    with pytest.raises(smoke.SmokeError, match="no tokens"):
        smoke.reply(t, "r", "hi")


def test_reply_surfaces_http_errors(smoke: ModuleType) -> None:
    t = FakeTransport({"POST /api/messages/r/reply": (429, b'{"detail": "slow down"}')})
    with pytest.raises(smoke.SmokeError, match="got 429"):
        smoke.reply(t, "r", "hi")


def test_run_checks_detects_sibling_leak(smoke: ModuleType, client: TestClient) -> None:
    class LeakyPath(ClientTransport):
        """Pretends the second branch's path also contains the first branch's nodes."""

        calls = 0

        def request(
            self, method: str, path: str, body: dict[str, Any] | None = None
        ) -> tuple[int, bytes]:
            status, raw = super().request(method, path, body)
            if path.endswith("/path"):
                self.calls += 1
                if self.calls == 2:
                    raw = raw.replace(b"[", b'[{"id": "leak"},', 1)
            return status, raw

    with pytest.raises(smoke.SmokeError, match="leaked"):
        smoke.run_checks(LeakyPath(client), log=lambda _line: None)
