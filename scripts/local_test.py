#!/usr/bin/env python3
"""Run the real-model (Ollama) tests on Brandon's PC and report to LOCAL_TESTS.md.

Steps: `git pull` → check Ollama is up and the model is pulled → check this
Python has the backend deps → `pytest -m ollama` in backend/ with LLM_PROVIDER=ollama →
append an entry to LOCAL_TESTS.md → commit only that file → rebase on
origin/main → push. `--no-push` does everything except commit and push.

Stdlib only and Windows-safe (no shell=True, pathlib paths, explicit utf-8),
so it runs before any project deps are installed. Pytest results are read
from a JUnit XML file rather than scraped from console output.

Usage:  python scripts/local_test.py [--no-push]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
REPORT = REPO_ROOT / "LOCAL_TESTS.md"
DEFAULT_BASE_URL = "http://localhost:11434/v1"
DEFAULT_MODEL = "llama3.1:8b"
MAX_FAILURE_LINES = 30


@dataclass
class Failure:
    name: str
    text: str


@dataclass
class TestRun:
    """Outcome of one pytest run, parsed from JUnit XML."""

    total: int = 0
    passed: int = 0
    skipped: int = 0
    failures: list[Failure] = field(default_factory=list)
    note: str | None = None  # set when the run couldn't happen (e.g. Ollama down)
    skip_reason: str | None = None  # message of the first skipped test, if any


def parse_junit(xml_text: str) -> TestRun:
    """Turn pytest's `--junitxml` output into a TestRun.

    Each <testcase> has at most one child: <failure>/<error> (failed),
    <skipped>, or none (passed).
    """
    root = ET.fromstring(xml_text)
    run = TestRun()
    for case in root.iter("testcase"):
        run.total += 1
        name = case.get("name", "?")
        cls = case.get("classname", "")
        full = f"{cls.rsplit('.', 1)[-1]}::{name}" if cls else name
        bad = case.find("failure")
        if bad is None:
            bad = case.find("error")
        if bad is not None:
            text = bad.text or bad.get("message") or ""
            run.failures.append(Failure(full, text))
        elif (skip := case.find("skipped")) is not None:
            run.skipped += 1
            if run.skip_reason is None:
                run.skip_reason = skip.get("message") or (skip.text or "").strip() or None
        else:
            run.passed += 1
    return run


def all_skipped_note(run: TestRun) -> str | None:
    """A 'not run' reason when every collected test was skipped, else None.

    Preflight checks Ollama before pytest starts, but the tests' own 1s probe
    can still fail (Ollama busy loading the model, a different base URL in the
    test env). Every test then skips, and without this the entry would read
    "0/N passed" with "Failures: none", which looks like a clean run and hides
    that nothing was tested.
    """
    if run.note or run.total == 0 or run.skipped != run.total:
        return None
    reason = f": {run.skip_reason}" if run.skip_reason else ""
    return f"all {run.total} `ollama` tests skipped{reason}"


def result_line(run: TestRun) -> str:
    if run.note:
        return f"not run — {run.note}"
    return f"{run.passed}/{run.total} passed ({run.skipped} skipped)"


def format_entry(run: TestRun, sha: str, model: str, now: datetime) -> str:
    """One LOCAL_TESTS.md entry, in the format CLAUDE.md defines."""
    lines = [
        f"## {now.strftime('%Y-%m-%d %H:%M')} UTC — local run",
        f"- Commit: {sha}",
        f"- Model: {model}",
        f"- Result: {result_line(run)}",
    ]
    if run.failures:
        lines.append("- Failures:")
        for f in run.failures:
            lines.append(f"  - {f.name}:")
            body = f.text.strip().splitlines()[:MAX_FAILURE_LINES] or ["(no output)"]
            fence = _fence(body)
            lines.append(f"    {fence}")
            lines.extend(f"    {line}" for line in body)
            lines.append(f"    {fence}")
    else:
        lines.append("- Failures: none")
    return "\n".join(lines) + "\n"


def append_entry(report: Path, entry: str) -> None:
    existing = report.read_text(encoding="utf-8") if report.exists() else ""
    sep = (
        ""
        if not existing or existing.endswith("\n\n")
        else "\n"
        if existing.endswith("\n")
        else "\n\n"
    )
    report.write_text(existing + sep + entry, encoding="utf-8", newline="\n")


def commit_message(run: TestRun, sha: str) -> str:
    if run.note:
        return f"local tests: not run on {sha}"
    return f"local tests: {run.passed}/{run.total} on {sha}"


def _with_tag(name: str) -> str:
    """Ollama treats an untagged name as `:latest`, so compare on that form."""
    return name if ":" in name.rsplit("/", 1)[-1] else f"{name}:latest"


def model_available(model: str, served: list[str]) -> bool:
    """True if `model` is among the ids Ollama listed (`llama3.1` == `llama3.1:latest`)."""
    want = _with_tag(model)
    return any(_with_tag(s) == want for s in served)


def preflight(base_url: str, model: str, served: list[str] | None) -> str | None:
    """Why the real-model tests can't run, or None if they can.

    A missing model would otherwise show up as every test failing with a 404,
    and the cloud agent would treat those as app bugs to fix.
    """
    if served is None:
        return f"Ollama not reachable at {base_url}"
    if not model_available(model, served):
        return f"model `{model}` not pulled (run `ollama pull {model}`)"
    return None


def _fence(body: list[str]) -> str:
    """A code fence longer than any backtick run in the body, so it can't close early."""
    longest = 0
    for line in body:
        run = 0
        for ch in line:
            run = run + 1 if ch == "`" else 0
            longest = max(longest, run)
    return "`" * max(3, longest + 1)


# ----------------------------------------------------------------- side effects


def git(*args: str, check: bool = True) -> str:
    out = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if check and out.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed:\n{out.stderr.strip()}")
    return out.stdout.strip()


def ollama_models(base_url: str) -> list[str] | None:
    """Model ids Ollama serves (`GET {base_url}/models`), or None if unreachable."""
    try:
        with urllib.request.urlopen(f"{base_url}/models", timeout=2) as r:
            body = json.loads(r.read())
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if not isinstance(body, dict):
        return None
    data = body.get("data")
    if not isinstance(data, list):
        return []
    return [str(m["id"]) for m in data if isinstance(m, dict) and "id" in m]


# What the backend tests import; checked with the same interpreter that runs pytest.
REQUIRED_MODULES = ("pytest", "fastapi", "sqlalchemy", "httpx")


def missing_deps_note(python: str = sys.executable) -> str | None:
    """Why pytest can't run with `python`, or None if the backend deps import.

    This script is stdlib-only, so it can be started with a Python that lacks
    the backend's packages (system Python instead of the project venv). pytest
    would then die before writing its results file, and the entry would only
    say "pytest crashed". Name the interpreter and the fix instead.
    """
    probe = (
        "import importlib.util, sys\n"
        f"missing = [m for m in {REQUIRED_MODULES!r} if importlib.util.find_spec(m) is None]\n"
        "print(' '.join(missing))\n"
    )
    try:
        proc = subprocess.run(
            [python, "-c", probe],
            cwd=BACKEND,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except OSError as exc:
        return f"could not start {python}: {exc}"
    missing = proc.stdout.strip() if proc.returncode == 0 else "(probe failed)"
    if not missing:
        return None
    return (
        f"backend deps missing for {python} ({missing}); run with the backend's venv "
        '(or `pip install -e ".[dev]"` in backend/)'
    )


def run_pytest(base_url: str, model: str) -> TestRun:
    deps = missing_deps_note()
    if deps:
        return TestRun(note=deps)
    env = {**os.environ, "LLM_PROVIDER": "ollama", "OLLAMA_BASE_URL": base_url}
    env["OLLAMA_MODEL"] = model
    with tempfile.TemporaryDirectory() as tmp:
        xml_path = Path(tmp) / "results.xml"
        cmd = [sys.executable, "-m", "pytest", "-m", "ollama", "-q", f"--junitxml={xml_path}"]
        proc = subprocess.run(cmd, cwd=BACKEND, env=env, check=False)
        if proc.returncode == 5:  # pytest: no tests collected
            return TestRun(note="no `ollama` tests collected")
        if not xml_path.exists():
            return TestRun(note=f"pytest crashed (exit {proc.returncode}), no results file")
        run = parse_junit(xml_path.read_text(encoding="utf-8"))
    run.note = all_skipped_note(run)
    return run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-push", action="store_true", help="write the report; no commit/push")
    args = parser.parse_args(argv)

    base_url = (os.getenv("OLLAMA_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    model = os.getenv("OLLAMA_MODEL") or DEFAULT_MODEL

    git("pull", "--rebase", "--autostash")
    sha = git("rev-parse", "--short", "HEAD")

    blocker = preflight(base_url, model, ollama_models(base_url))
    run = TestRun(note=blocker) if blocker else run_pytest(base_url, model)

    entry = format_entry(run, sha, model, datetime.now(UTC))
    append_entry(REPORT, entry)
    print(entry)

    if args.no_push:
        print("--no-push: report written, not committed.")
    else:
        git("add", "--", REPORT.name)
        git("commit", "-m", commit_message(run, sha), "--", REPORT.name)
        git("pull", "--rebase", "--autostash", "origin", "main")
        git("push", "origin", "HEAD:main")
        print("Report committed and pushed.")
    return 1 if run.failures else 0


if __name__ == "__main__":
    sys.exit(main())
