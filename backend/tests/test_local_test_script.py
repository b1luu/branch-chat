"""Unit tests for scripts/local_test.py report formatting (no git, no pytest subprocess)."""

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Self

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "local_test.py"


@pytest.fixture(scope="module")
def lt() -> ModuleType:
    spec = importlib.util.spec_from_file_location("local_test", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["local_test"] = mod  # dataclasses need the module registered
    spec.loader.exec_module(mod)
    return mod


NOW = datetime(2026, 10, 28, 9, 5, tzinfo=UTC)

FAKE_JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="5" failures="1" errors="1" skipped="1">
  <testcase classname="tests.integration.test_ollama_live" name="test_streams"/>
  <testcase classname="tests.integration.test_ollama_live" name="test_persisted"/>
  <testcase classname="tests.integration.test_ollama_live" name="test_siblings">
    <failure message="AssertionError: leak">{trace}</failure>
  </testcase>
  <testcase classname="tests.integration.test_ollama_live" name="test_quote">
    <error message="fixture blew up">E   RuntimeError: boom</error>
  </testcase>
  <testcase classname="tests.integration.test_ollama_live" name="test_merge">
    <skipped type="pytest.skip" message="D1 not implemented">skip</skipped>
  </testcase>
</testsuite></testsuites>
"""


def junit(trace: str = "E   assert 'SIBLING' not in prompt") -> str:
    return FAKE_JUNIT.format(trace=trace)


def test_parse_junit_counts(lt: ModuleType) -> None:
    run = lt.parse_junit(junit())
    assert (run.total, run.passed, run.skipped) == (5, 2, 1)
    assert [f.name for f in run.failures] == [
        "test_ollama_live::test_siblings",
        "test_ollama_live::test_quote",
    ]
    assert "SIBLING" in run.failures[0].text
    assert "RuntimeError: boom" in run.failures[1].text


def test_format_entry_matches_claude_md_format(lt: ModuleType) -> None:
    entry = lt.format_entry(lt.parse_junit(junit()), "abc1234", "llama3.1:8b", NOW)
    lines = entry.splitlines()
    assert lines[:5] == [
        "## 2026-10-28 09:05 UTC — local run",
        "- Commit: abc1234",
        "- Model: llama3.1:8b",
        "- Result: 2/5 passed (1 skipped)",
        "- Failures:",
    ]
    assert "  - test_ollama_live::test_siblings:" in lines
    assert "    E   assert 'SIBLING' not in prompt" in lines
    assert entry.endswith("\n")


def test_failure_output_truncated_to_30_lines(lt: ModuleType) -> None:
    trace = "\n".join(f"line {i}" for i in range(100))
    entry = lt.format_entry(lt.parse_junit(junit(trace)), "s", "m", NOW)
    assert "    line 29" in entry
    assert "line 30" not in entry


def test_all_passing_run(lt: ModuleType) -> None:
    run = lt.TestRun(total=4, passed=4)
    entry = lt.format_entry(run, "s", "m", NOW)
    assert "- Result: 4/4 passed (0 skipped)" in entry
    assert "- Failures: none" in entry
    assert lt.commit_message(run, "s") == "local tests: 4/4 on s"


def test_not_run_note(lt: ModuleType) -> None:
    run = lt.TestRun(note="Ollama not reachable at http://localhost:11434/v1")
    entry = lt.format_entry(run, "s", "m", NOW)
    assert "- Result: not run — Ollama not reachable" in entry
    assert lt.commit_message(run, "s") == "local tests: not run on s"


@pytest.mark.parametrize("existing", ["", "# Local tests\n", "# Local tests\n\n", "# x"])
def test_append_entry_keeps_one_blank_line_between_entries(
    lt: ModuleType, tmp_path: Path, existing: str
) -> None:
    report = tmp_path / "LOCAL_TESTS.md"
    if existing:
        report.write_text(existing, encoding="utf-8")
    lt.append_entry(report, "## new\n")
    text = report.read_text(encoding="utf-8")
    assert text.endswith("## new\n")
    if existing:
        assert text == existing.rstrip("\n") + "\n\n## new\n"


# --- Q22: preflight (model pulled?) and fences that survive backticks in output


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def _serve(lt: ModuleType, monkeypatch: pytest.MonkeyPatch, body: bytes | Exception) -> list[str]:
    urls: list[str] = []

    def fake_urlopen(url: str, timeout: float) -> _FakeResponse:
        urls.append(url)
        if isinstance(body, Exception):
            raise body
        return _FakeResponse(body)

    monkeypatch.setattr(lt.urllib.request, "urlopen", fake_urlopen)
    return urls


def test_ollama_models_lists_ids(lt: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    body = b'{"object":"list","data":[{"id":"llama3.1:8b"},{"id":"qwen2.5"},{"x":1},"junk"]}'
    urls = _serve(lt, monkeypatch, body)
    assert lt.ollama_models("http://h:11434/v1") == ["llama3.1:8b", "qwen2.5"]
    assert urls == ["http://h:11434/v1/models"]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (OSError("refused"), None),
        (b"not json", None),
        (b"[1, 2]", None),
        (b'{"object":"list"}', []),
    ],
)
def test_ollama_models_unreachable_or_odd(
    lt: ModuleType, monkeypatch: pytest.MonkeyPatch, body: bytes | Exception, expected: object
) -> None:
    _serve(lt, monkeypatch, body)
    assert lt.ollama_models("http://h/v1") == expected


@pytest.mark.parametrize(
    ("model", "served", "ok"),
    [
        ("llama3.1:8b", ["llama3.1:8b"], True),
        ("llama3.1:8b", ["llama3.1:70b", "llama3.1:latest"], False),
        ("llama3.1", ["llama3.1:latest"], True),
        ("llama3.1:latest", ["llama3.1"], True),
        ("llama3.1", ["llama3.1:8b"], False),
        ("reg.local:5000/m", ["reg.local:5000/m:latest"], True),
        ("llama3.1:8b", [], False),
    ],
)
def test_model_available(lt: ModuleType, model: str, served: list[str], ok: bool) -> None:
    assert lt.model_available(model, served) is ok


def test_preflight_reasons(lt: ModuleType) -> None:
    url = "http://localhost:11434/v1"
    assert lt.preflight(url, "llama3.1:8b", None) == f"Ollama not reachable at {url}"
    missing = lt.preflight(url, "llama3.1:8b", ["mistral:latest"])
    assert missing == "model `llama3.1:8b` not pulled (run `ollama pull llama3.1:8b`)"
    assert lt.preflight(url, "llama3.1:8b", ["llama3.1:8b"]) is None


def test_missing_model_reports_not_run_instead_of_failures(
    lt: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Missing model → one 'not run' entry; pytest never runs, so no fake 404 failures."""
    _serve(lt, monkeypatch, b'{"data":[{"id":"mistral:latest"}]}')
    monkeypatch.setattr(lt, "REPORT", tmp_path / "LOCAL_TESTS.md")
    monkeypatch.setattr(lt, "git", lambda *a, **k: "abc1234")
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)

    def no_pytest(*a: object) -> None:
        raise AssertionError("pytest must not run when the model is missing")

    monkeypatch.setattr(lt, "run_pytest", no_pytest)
    assert lt.main(["--no-push"]) == 0
    text = (tmp_path / "LOCAL_TESTS.md").read_text(encoding="utf-8")
    assert "- Result: not run — model `llama3.1:8b` not pulled" in text
    assert "- Failures: none" in text


def test_failure_with_backticks_keeps_fence_closed(lt: ModuleType) -> None:
    trace = "E   AssertionError: reply was\nE   ```python\nE   x = 1\nE   ````\nE   end"
    entry = lt.format_entry(lt.parse_junit(junit(trace)), "abc1234", "m", NOW)
    lines = entry.splitlines()
    start = lines.index("    `````")  # longest run in the body is 4 → fence of 5
    end = lines.index("    `````", start + 1)
    assert lines[start + 1 : end][-1] == "    E   end"
    assert "    ```" not in lines[:start]  # the fence opens with the long one, not ```


def test_plain_failure_keeps_three_backtick_fence(lt: ModuleType) -> None:
    entry = lt.format_entry(lt.parse_junit(junit()), "abc1234", "m", NOW)
    assert "    ```\n    E   assert 'SIBLING' not in prompt\n    ```" in entry


# --- Q37: runs that tested nothing are reported as "not run", with the reason ---

ALL_SKIPPED_JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="2" skipped="2">
  <testcase classname="tests.integration.test_ollama_live" name="test_streams">
    <skipped type="pytest.skip" message="Ollama not reachable at http://localhost:11434/v1">x</skipped>
  </testcase>
  <testcase classname="tests.integration.test_ollama_live" name="test_merge">
    <skipped type="pytest.skip" message="Ollama not reachable at http://localhost:11434/v1">x</skipped>
  </testcase>
</testsuite></testsuites>
"""


def test_parse_junit_keeps_first_skip_reason(lt: ModuleType) -> None:
    assert lt.parse_junit(junit()).skip_reason == "D1 not implemented"
    assert lt.parse_junit(ALL_SKIPPED_JUNIT).skip_reason.startswith("Ollama not reachable")


def test_all_skipped_note(lt: ModuleType) -> None:
    run = lt.parse_junit(ALL_SKIPPED_JUNIT)
    assert lt.all_skipped_note(run) == (
        "all 2 `ollama` tests skipped: Ollama not reachable at http://localhost:11434/v1"
    )
    # Some tests ran (or failed): a normal result, not "not run".
    assert lt.all_skipped_note(lt.parse_junit(junit())) is None
    assert lt.all_skipped_note(lt.TestRun()) is None  # nothing collected is its own note
    assert lt.all_skipped_note(lt.TestRun(total=1, skipped=1)) == "all 1 `ollama` tests skipped"


def _fake_pytest(lt: ModuleType, monkeypatch: pytest.MonkeyPatch, xml: str | None, code: int):
    calls: list[list[str]] = []

    class Done:
        returncode = code

    def fake_run(cmd: list[str], **kwargs: object) -> Done:
        calls.append(cmd)
        if xml is not None:
            out = next(a for a in cmd if a.startswith("--junitxml=")).split("=", 1)[1]
            Path(out).write_text(xml, encoding="utf-8")
        return Done()

    monkeypatch.setattr(lt, "missing_deps_note", lambda: None)
    monkeypatch.setattr(lt.subprocess, "run", fake_run)
    return calls


def test_run_pytest_all_skipped_is_not_run(
    lt: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every test skipped → 'not run' with the reason, never a clean-looking 0/N."""
    calls = _fake_pytest(lt, monkeypatch, ALL_SKIPPED_JUNIT, 0)
    run = lt.run_pytest("http://localhost:11434/v1", "llama3.1:8b")
    assert calls and "-m" in calls[0] and "ollama" in calls[0]
    assert run.note.startswith("all 2 `ollama` tests skipped: Ollama not reachable")
    entry = lt.format_entry(run, "abc1234", "llama3.1:8b", NOW)
    assert "- Result: not run — all 2 `ollama` tests skipped: Ollama not reachable" in entry
    assert lt.commit_message(run, "abc1234") == "local tests: not run on abc1234"


def test_run_pytest_real_results_have_no_note(
    lt: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_pytest(lt, monkeypatch, junit(), 1)
    run = lt.run_pytest("http://localhost:11434/v1", "llama3.1:8b")
    assert run.note is None
    assert lt.result_line(run) == "2/5 passed (1 skipped)"


def test_run_pytest_missing_deps_skips_pytest(
    lt: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_pytest(lt, monkeypatch, None, 1)
    monkeypatch.setattr(lt, "missing_deps_note", lambda: "backend deps missing for py (pytest)")
    run = lt.run_pytest("http://localhost:11434/v1", "llama3.1:8b")
    assert calls == []
    assert run.note == "backend deps missing for py (pytest)"
    assert run.failures == []


def test_missing_deps_note_with_this_python(lt: ModuleType) -> None:
    """The test env has the backend deps, so the probe finds nothing missing."""
    assert lt.missing_deps_note(sys.executable) is None


def test_missing_deps_note_names_missing_module(
    lt: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lt, "REQUIRED_MODULES", ("pytest", "no_such_module_q37"))
    note = lt.missing_deps_note(sys.executable)
    assert note is not None
    assert "no_such_module_q37" in note and "pytest " not in note.split("(", 1)[1]
    assert sys.executable in note and 'pip install -e ".[dev]"' in note


def test_missing_deps_note_unstartable_python(lt: ModuleType, tmp_path: Path) -> None:
    missing = str(tmp_path / "no-python-here")
    note = lt.missing_deps_note(missing)
    assert note is not None and note.startswith(f"could not start {missing}")
