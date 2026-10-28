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

## 2026-10-27 21:46 UTC — B4
- Status: green
- Done: `POST/GET /api/conversations`, `GET /api/conversations/{id}/tree`, `GET /api/messages/{id}/path` (routes in `app/api/routes.py`, Pydantic schemas in `app/api/schemas.py`, `create_conversation`/`list_conversations`/`get_tree` in the repository). 13 new API tests (defaults, validation, ordering, branch shape, no cross-conversation leaks, preview truncation, quote in path, 404s).
- Decisions: creating a conversation also creates a root **system** message (body `{title?, system_prompt?}`, response includes `root_message_id`), so the first user turn is just `POST /messages/{root}/reply` and "reply" stays the only way messages are created. Tree is a flat list ordered parents-first (client builds the tree from `parent_id`) with an 80-char single-line preview instead of full content. Session factory lives on `app.state` and `create_app(session_factory)` takes an override, so tests bind the app to the per-test in-memory DB without globals. Schema creation is left to Alembic (S1), not app startup.
- Next: B5

## 2026-10-27 21:46 UTC — B5
- Status: green
- Done: `app/llm/`: `LLMProvider` ABC with `stream(messages, max_tokens) -> AsyncIterator[str]`, `ChatMessage` dataclass, `MockProvider`, `get_provider()` factory reading `LLM_PROVIDER`. 12 tests (multi-chunk stream joins to full reply, determinism, context count, max_tokens cap, factory default/env/override/errors).
- Decisions: mock reply is `Mock reply to "<last user msg, 60 chars>" with N context messages.`, streamed one word per chunk so streaming and `max_tokens` truncation are testable. Factory defaults to mock and raises `ProviderConfigError` for unknown names; `ollama`/`anthropic` raise "not implemented yet" until B7 (B7 should replace `test_factory_real_providers_not_yet_available` with tests that those names build the real providers — that's a behavior change, not a weakened test).
- Next: B6

## 2026-10-27 21:48 UTC — B6
- Status: green
- Done: `POST /api/messages/{id}/reply` (body `{content, quote?}`): commits the user node, streams SSE `user` → `token`* → `done` (or `error`), persists the assistant node on completion. Prompt building in `app/services/chat.py` (`build_prompt` from the CTE path; quotes inlined as a fenced excerpt). 12 tests: stream shape, both nodes persisted with right parents, context = exact path (recording provider), branching from a non-leaf isolates siblings, quote stored + in prompt, `LLM_MAX_TOKENS` passed through, 404/422, provider error → `error` event and no assistant row.
- Decisions: SSE payloads are JSON (`data: {"text": ...}`) so tokens containing newlines can't break framing. User node is committed *before* the stream starts so 404s/validation still return proper status codes; the stream uses its own session (the request session isn't safe to rely on after the handler returns). Assistant node is written only when generation completes (no half replies on error/disconnect); the user turn is kept so the client can retry. Provider is chosen once at startup and stored on `app.state` (`create_app(session_factory, llm_provider)` for tests). Replies are allowed under any role (no user→user check yet; revisit in S4).
- Next: B7

## 2026-10-28 15:46 UTC — B7
- Status: green
- Done: `OllamaProvider` (OpenAI-compatible `/v1/chat/completions`, `OLLAMA_BASE_URL`/`OLLAMA_MODEL`, default `llama3.1:8b`) and `AnthropicProvider` (Messages API, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` default `claude-haiku-5-5`), both streaming over SSE via a shared `iter_sse_json` helper; factory now builds them. 21 new tests with respx (payloads/headers, chunking, `[DONE]`, HTTP/stream/connection/timeout errors → `ProviderError`, and the reply endpoint end-to-end against mocked Ollama). Added `.env.example` and a root `.gitignore` for `.env`.
- Decisions: plain httpx instead of the anthropic/openai SDKs (one HTTP client, easy respx mocking, no extra deps). Anthropic payload moves system turns into top-level `system` and merges consecutive same-role turns (API requires alternation). `build_payload` is a separate public method so D4 (prompt caching) and B8 (capture the prompt) can hook it. Read timeout 120s for slow first tokens on a cold local GPU. `ProviderConfigError` moved to `base.py` (still exported from `app.llm`); missing Anthropic key fails at startup, not on first request. Replaced `test_factory_real_providers_not_yet_available` with tests that the factory builds the real providers (behavior change planned in B5).
- Deps: `httpx` moved from dev to runtime; `respx` added to dev (HTTP mocking).
- Next: B8
