"""Q12: pinned backend dependencies.

`pyproject.toml` only declares ranges (`fastapi>=0.115`), so every `pip install`
resolved to whatever was newest that day: CI, Docker and Brandon's PC could all
test different versions. `requirements.lock` (runtime, Docker) and
`requirements-dev.lock` (runtime + test tools, CI and local dev) pin everything.

These tests keep the locks honest without touching the network: exact pins only,
every declared dependency is locked at a version its range allows, the dev lock
pins the runtime packages at the same versions as the runtime lock, and the files
are "universal" (platform markers, so one lock installs on Linux and Windows).
"""

import re
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
RUNTIME_LOCK = BACKEND / "requirements.lock"
DEV_LOCK = BACKEND / "requirements-dev.lock"

# `name==version`, optional `; marker`, optional trailing `# via ...` comment.
PIN = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)==(?P<version>\S+?)\s*(?:;\s*(?P<marker>[^#]+?))?\s*(?:#.*)?$")


def parse_lock(path: Path) -> dict[str, tuple[str, str | None]]:
    """Map canonical package name -> (version, marker or None). Fails on any non-pin line."""
    pins: dict[str, tuple[str, str | None]] = {}
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = PIN.match(line)
        assert m, f"{path.name}:{n} is not an exact pin: {raw!r}"
        name = canonicalize_name(m["name"])
        assert name not in pins, f"{path.name}: {name} pinned twice"
        pins[name] = (m["version"], m["marker"])
    return pins


def declared(group: str | None = None) -> list[Requirement]:
    project = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    specs = project["dependencies"] if group is None else project["optional-dependencies"][group]
    return [Requirement(s) for s in specs]


@pytest.fixture(scope="module")
def runtime() -> dict[str, tuple[str, str | None]]:
    return parse_lock(RUNTIME_LOCK)


@pytest.fixture(scope="module")
def dev() -> dict[str, tuple[str, str | None]]:
    return parse_lock(DEV_LOCK)


def assert_satisfies(pins: dict[str, tuple[str, str | None]], req: Requirement) -> None:
    name = canonicalize_name(req.name)
    assert name in pins, f"{req} is declared in pyproject.toml but missing from the lock"
    version = pins[name][0]
    assert req.specifier.contains(version, prereleases=True), (
        f"lock pins {name}=={version}, outside pyproject's {req.specifier}: regenerate the lock"
    )


def test_locks_contain_only_exact_pins(
    runtime: dict[str, tuple[str, str | None]], dev: dict[str, tuple[str, str | None]]
) -> None:
    assert runtime and dev  # parse_lock already rejected anything that isn't `name==version`


@pytest.mark.parametrize("req", declared(), ids=str)
def test_runtime_deps_locked_within_range(
    req: Requirement, runtime: dict[str, tuple[str, str | None]]
) -> None:
    assert_satisfies(runtime, req)


@pytest.mark.parametrize("req", declared() + declared("dev"), ids=str)
def test_dev_lock_covers_runtime_and_dev_deps(
    req: Requirement, dev: dict[str, tuple[str, str | None]]
) -> None:
    assert_satisfies(dev, req)


def test_extras_are_locked(runtime: dict[str, tuple[str, str | None]]) -> None:
    """`psycopg[binary]` and `uvicorn[standard]` pull in packages under other names."""
    for pkg in ("psycopg-binary", "httptools", "watchfiles", "websockets"):
        assert pkg in runtime, pkg


def test_dev_lock_pins_runtime_packages_at_same_versions(
    runtime: dict[str, tuple[str, str | None]], dev: dict[str, tuple[str, str | None]]
) -> None:
    """CI installs the dev lock, Docker the runtime one: they must be the same code."""
    for name, pin in runtime.items():
        assert dev.get(name) == pin, f"{name}: runtime {pin} vs dev {dev.get(name)}"


def test_locks_are_universal(
    runtime: dict[str, tuple[str, str | None]], dev: dict[str, tuple[str, str | None]]
) -> None:
    """Resolved for every platform, not just the Linux box that generated it.

    uvloop has no Windows build, so a Linux-only lock would either list it
    unconditionally (install fails on Brandon's PC) or drop Windows-only deps.
    """
    uvloop_marker = runtime["uvloop"][1]
    assert uvloop_marker and "sys_platform != 'win32'" in uvloop_marker
    colorama_marker = dev["colorama"][1]  # pytest's Windows-only dependency
    assert colorama_marker and "sys_platform == 'win32'" in colorama_marker


def test_lock_headers_say_how_to_regenerate() -> None:
    for path, extra in ((RUNTIME_LOCK, ""), (DEV_LOCK, " --extra dev")):
        text = path.read_text(encoding="utf-8")
        assert f"uv pip compile pyproject.toml{extra} --universal" in text
        assert f"-o {path.name}" in text


def test_dockerfile_installs_from_runtime_lock() -> None:
    dockerfile = (BACKEND / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY pyproject.toml requirements.lock ./" in dockerfile
    assert "pip install -r requirements.lock" in dockerfile
    # Installing the project itself must not re-resolve deps past the pins.
    assert "pip install --no-deps ." in dockerfile
    assert "pip install . " not in dockerfile and not dockerfile.rstrip().endswith("pip install .")


def test_readme_setup_uses_dev_lock() -> None:
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "pip install -r requirements-dev.lock" in readme
    assert 'pip install -e ".[dev]"' not in readme
