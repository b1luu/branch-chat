#!/usr/bin/env python3
"""Run the real-model (Ollama) tests on Brandon's PC and report to LOCAL_TESTS.md.

Steps: `git pull` → `pytest -m ollama` in backend/ with LLM_PROVIDER=ollama →
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
        elif case.find("skipped") is not None:
            run.skipped += 1
        else:
            run.passed += 1
    return run


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
            lines.append("    ```")
            lines.extend(f"    {line}" for line in body)
            lines.append("    ```")
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


def ollama_reachable(base_url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url}/models", timeout=2) as r:
            json.loads(r.read())
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def run_pytest(base_url: str, model: str) -> TestRun:
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
        return parse_junit(xml_path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-push", action="store_true", help="write the report; no commit/push")
    args = parser.parse_args(argv)

    base_url = (os.getenv("OLLAMA_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    model = os.getenv("OLLAMA_MODEL") or DEFAULT_MODEL

    git("pull", "--rebase", "--autostash")
    sha = git("rev-parse", "--short", "HEAD")

    if ollama_reachable(base_url):
        run = run_pytest(base_url, model)
    else:
        run = TestRun(note=f"Ollama not reachable at {base_url}")

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
