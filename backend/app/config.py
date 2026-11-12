"""Parsing numeric settings from the environment (Q36).

Every numeric knob (`LLM_MAX_TOKENS`, `CONTEXT_TOKEN_BUDGET`, `RATE_LIMIT_BURST`,
...) goes through `env_int` / `env_float` so they all behave the same way:

* **Unset or blank means the default.** `.env` files often carry `NAME=` with
  no value (that's how `.env.example` leaves `ANTHROPIC_API_KEY`), and docker
  compose passes it through as an empty string. A bare `int("")` raised on
  every request instead, so each reply was a 500.
* **Anything else invalid is a `ConfigError` naming the variable.** `create_app`
  reads every setting once at startup, so a typo like `60k` stops the API from
  starting with a clear message instead of failing request by request while
  the health check stays green.
"""

import math
import os


class ConfigError(ValueError):
    """An environment setting has a value the app can't use."""


def _raw(name: str) -> str | None:
    raw = os.getenv(name)
    return None if raw is None or not raw.strip() else raw.strip()


def env_int(name: str, default: int, minimum: int = 1) -> int:
    """`name` as an int >= `minimum`; `default` if unset or blank."""
    raw = _raw(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        value = None
    if value is None or value < minimum:
        raise ConfigError(f"{name} must be an integer >= {minimum}, got {raw!r}")
    return value


def env_float(name: str, default: float, minimum: float = 0.0) -> float:
    """`name` as a finite float >= `minimum`; `default` if unset or blank."""
    raw = _raw(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value < minimum:
        raise ConfigError(f"{name} must be a number >= {minimum:g}, got {raw!r}")
    return value
