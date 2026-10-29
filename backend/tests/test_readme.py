"""S3: README has what SPEC's definition of done asks for, and its links resolve."""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def readme() -> str:
    return (REPO / "README.md").read_text(encoding="utf-8")


def test_has_mermaid_architecture_diagram(readme: str) -> None:
    block = re.search(r"```mermaid\n(.*?)```", readme, re.DOTALL)
    assert block and block.group(1).lstrip().startswith("flowchart")


@pytest.mark.parametrize(
    "heading",
    [
        "## Architecture",
        "## Quick start (Docker)",
        "### With a local model (Ollama)",
        "### With Claude Haiku",
        "## Tests",
        "## Design decisions",
    ],
)
def test_has_section(readme: str, heading: str) -> None:
    assert f"\n{heading}\n" in readme


@pytest.mark.parametrize(
    "topic", ["Tree model", "Recursive CTE", "Prompt caching", "Context budget"]
)
def test_design_decisions_cover(readme: str, topic: str) -> None:
    assert f"**{topic}" in readme


def test_has_screenshot_placeholder(readme: str) -> None:
    assert "Screenshot / GIF placeholder" in readme or re.search(r"!\[[^\]]*\]\(", readme)


def test_relative_links_exist(readme: str) -> None:
    for target in re.findall(r"\]\((?!https?://|#)([^)\s]+)\)", readme):
        assert (REPO / target).exists(), target


def test_documented_env_vars_exist_in_env_example(readme: str) -> None:
    example = (REPO / ".env.example").read_text(encoding="utf-8")
    table = readme.split("## Configuration", 1)[1].split("\n## ", 1)[0]
    for var in re.findall(r"`([A-Z][A-Z0-9_]{2,})`", table):
        assert f"\n{var}=" in example, var
