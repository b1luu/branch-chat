# Progress log

Newest entries at the bottom. Each sprint appends one entry (format in CLAUDE.md).

## 2026-10-27 18:15 UTC — setup
- Status: green (nothing built yet)
- Done: added SPEC.md, BACKLOG.md, CLAUDE.md, PROGRESS.md
- Decisions: FastAPI + React; mock LLM provider in tests; SQLite for tests, Postgres for real use
- Next: B1

## 2026-10-27 19:46 UTC — B1
- Status: red (on wip/B1) — environment blocker, not a code failure
- Done: wrote backend scaffold (pyproject with ruff/pytest/pytest-asyncio/httpx, FastAPI app factory, `/healthz`, healthz test, app/{api,db,llm,services} packages). `ruff check .` passes. Pushed to `wip/B1` (2be26a7).
- Error: `pip`/`uv` install of fastapi failed: `Tunnel connection failed: 403 Forbidden` for pypi.org (direct DNS to pypi.org also fails). Proxy status shows `connect_rejected ... host: pypi.org:443`. Without fastapi, pytest fails at collection (`ModuleNotFoundError: No module named 'fastapi'`).
- Tried: uv install, pip install, pip via proxy with pypi removed from NO_PROXY, searched local caches for wheels. No fix possible from inside the sandbox; did not count against the 5 code-fix attempts.
- Decisions: did not mark B1 BLOCKED in BACKLOG (it's the cloud environment, not the item). Did not merge wip/B1 to main because tests couldn't run. Next sprint: if PyPI is reachable, check out wip/B1, run the full check, and merge if green.
- Next: B1 (needs package registry access for the cloud environment)

## 2026-10-27 19:53 UTC — B1
- Status: green
- Done: merged wip/B1 into main: FastAPI app factory, `/healthz` + test, pyproject (ruff, pytest, pytest-asyncio, httpx). `ruff check .` and `pytest -q` pass (1 passed).
- Decisions: earlier PyPI failure was the sandbox network, not code. Fix for cloud runs: pypi.org is in NO_PROXY but direct DNS fails, so install with `NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 pip install -e ".[dev]"` to route through the proxy. Starlette warns to use httpx2 for TestClient; kept httpx because respx (B7) mocks httpx.
- Next: B2

## 2026-10-27 19:54 UTC — B2
- Status: green
- Done: `Conversation`, `Message` (adjacency list via `parent_id`, optional `quote`), `Note` (fork-origin `message_id` + `source_leaf_id`) models; engine/session factory; Alembic config + initial migration `0001`; in-memory SQLite fixtures. Tests: tree relationships, defaults, FK enforcement, notes, cascade delete, migration upgrade matches models with no drift, and downgrade.
- Decisions: hex UUID string ids (no autoincrement exposure, same on SQLite/Postgres). SQLite FK enforcement turned on per connection (`PRAGMA foreign_keys=ON`), otherwise SQLite ignores FKs. In-memory test DB uses StaticPool so all sessions share one connection. Migration test compares Alembic's schema to the ORM so they can't drift. Ruff isort told `alembic` is third-party (the local `alembic/` dir confused it).
- Next: B3

## 2026-10-27 19:56 UTC — B3
- Status: green
- Done: `get_path(db, message_id)` in `app/db/repository.py`: one recursive CTE that walks up `parent_id` from the node, ordered root-first. `NotFoundError` domain error mapped to HTTP 404 by an app exception handler. Tests: linear chain, branch siblings don't leak, other conversations don't leak, root-only, 200-deep chain, missing id → NotFoundError and → 404.
- Decisions: CTE walks *up* (anchor = target node) so cost is O(depth), independent of tree size. Path tests are parametrized over SQLite and Postgres; the Postgres leg runs only when `TEST_POSTGRES_URL` is set (verified this sprint against a local Postgres 16: 21/21 passed). New dependency: `psycopg[binary]` (Postgres driver, needed for real use and S1).
- Env note for cloud runs: install deps with `NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 pip install -e ".[dev]"` (direct pypi.org DNS fails; the proxy works).
- Next: B4
