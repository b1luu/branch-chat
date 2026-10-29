"""S2: the CI workflow runs the same checks as the sprint loop in CLAUDE.md.

Plain-text checks (no YAML dependency): guards against the workflow drifting
from the commands contributors and agents run locally.
"""

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"


@pytest.fixture(scope="module")
def ci() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_triggers_on_push_and_pull_request(ci: str) -> None:
    assert "\non:\n" in ci
    assert "  push:" in ci and "  pull_request:" in ci


@pytest.mark.parametrize(
    "command",
    [
        'pip install -e ".[dev]"',
        "ruff check .",
        "pytest -q",
        "npm ci",
        "npm run lint",
        "npm test -- --run",
        "npm run build",
        "python3 scripts/smoke_test.py --compose",
    ],
)
def test_runs_every_check(ci: str, command: str) -> None:
    assert f"- run: {command}\n" in ci


def test_backend_tests_use_mock_llm_and_postgres(ci: str) -> None:
    assert "LLM_PROVIDER: mock" in ci
    assert "TEST_POSTGRES_URL: postgresql+psycopg://" in ci
    assert "image: postgres:16-alpine" in ci


def test_sprint_loop_commands_are_all_in_ci(ci: str) -> None:
    """Every command in CLAUDE.md's full check must also run in CI."""
    rules = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
    for line in rules.splitlines():
        if line.strip().startswith(("- backend:", "- frontend")):
            for cmd in line.split("`")[1].split("&&")[1:]:  # drop the leading `cd <dir>`
                assert f"- run: {cmd.strip()}\n" in ci, cmd


def test_no_secrets_referenced(ci: str) -> None:
    assert "secrets." not in ci
