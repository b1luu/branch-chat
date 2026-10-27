# Backlog

Work top to bottom. One item (or a few tiny ones) per sprint. Check an item off only when its tests pass.
Each item lists its acceptance criteria. Don't reorder unless an item is blocked; if blocked, note why in PROGRESS.md and move on.

## Phase 1 — Backend foundation
- [x] **B1 Scaffold backend**: `backend/` with FastAPI app, `pyproject.toml` (ruff, pytest, pytest-asyncio, httpx), `/healthz`. Test: healthz returns 200.
- [x] **B2 Data model**: SQLAlchemy models `Conversation`, `Message`, `Note`; Alembic initial migration; SQLite test fixture. Test: create rows, FK + parent relationships.
- [x] **B3 Path query**: repository function `get_path(message_id)` using a recursive CTE, works on SQLite and Postgres. Tests: linear chain, branch siblings don't leak, root-only, missing id → 404.
- [x] **B4 Conversation + tree endpoints**: create/list conversations, `GET /tree`, `GET /messages/{id}/path`. Tests for each.
- [x] **B5 LLM provider interface**: `LLMProvider`, `MockProvider` (echoes a deterministic reply that mentions how many context messages it received), factory from `LLM_PROVIDER`. Tests.
- [ ] **B6 Reply endpoint with SSE**: `POST /messages/{id}/reply` persists user node, streams assistant tokens, persists assistant node; quote included in prompt. Tests: stream content, both nodes persisted, branching from a non-leaf works, context = correct path.
- [ ] **B7 Ollama + Anthropic providers**: real implementations behind the interface; unit tests with HTTP mocked (respx). No real network calls in tests. Ollama reads `OLLAMA_BASE_URL` and `OLLAMA_MODEL` (default `llama3.1:8b`).
- [ ] **B8 Ollama integration tests**: `backend/tests/integration/` with a registered pytest marker `ollama`. A fixture pings `{OLLAMA_BASE_URL}/models` (1s timeout) and **skips** every `ollama` test if unreachable, so cloud runs stay green. Real model output is random: assert structure, not exact text. Cover: reply streams more than one chunk and is non-empty; user + assistant nodes persisted; a branch's prompt contains its ancestors and not its siblings (capture the request the provider sends); quote text appears in the prompt; merge-back produces a non-empty note. Keep each test under ~60s.
- [ ] **L1 Local test runner**: `scripts/local_test.py` (stdlib only, must work on Windows). It runs `git pull`, then `pytest -m ollama -q` in `backend/` with `LLM_PROVIDER=ollama`, then appends an entry to `LOCAL_TESTS.md` (format in CLAUDE.md) with the commit SHA, model, pass/fail counts, and the first ~30 lines of each failure. Then it commits only `LOCAL_TESTS.md`, rebases on origin/main, and pushes. Flag `--no-push` for dry runs. Unit-test the report formatting with a fake pytest result.

## Phase 2 — Frontend MVP
- [ ] **F1 Scaffold frontend**: `frontend/` Vite + React + TS, Vitest, ESLint; API client module; dev proxy to backend. Test: app renders.
- [ ] **F2 Chat view**: render active path, input box, streaming assistant reply via SSE. Tests with mocked fetch.
- [ ] **F3 Tree sidebar**: collapsible tree from `/tree`; click node → becomes active leaf; active path highlighted. Tests.
- [ ] **F4 Ask about this**: select text in an assistant message → button → creates quote branch from that message. Tests.
- [ ] **F5 Branch navigation**: breadcrumb + "Back to main thread" (returns to the deepest leaf of the branch you came from). Tests.

## Phase 3 — The differentiators
- [ ] **D1 Merge back**: backend summarize endpoint (uses provider) + note rendering in main thread + notes included in descendants' context. Tests both sides.
- [ ] **D2 Context budget**: token estimate + ancestor summarization when over budget. Tests with a tiny budget.
- [ ] **D3 Rate limiting**: token-bucket middleware per IP on reply/merge endpoints, 429 with Retry-After. Tests.
- [ ] **D4 Prompt caching**: Anthropic provider marks the shared ancestor prefix cacheable. Unit test on the request payload.

## Phase 4 — Ship it
- [ ] **S1 Docker**: Dockerfiles + `docker-compose.yml` (postgres, api, web); Alembic runs on start. Smoke test script.
- [ ] **S2 CI**: GitHub Actions running ruff, pytest, eslint, vitest, frontend build on push/PR.
- [ ] **S3 README**: pitch, Mermaid architecture diagram, setup (local Ollama + Haiku), design decisions (tree model, recursive CTE, caching, budget), screenshot placeholder.
- [ ] **S4 Hardening pass**: error states in UI, empty states, input validation, loading indicators. Tests for each.

## After the backlog is done
Don't invent big features. Pick the weakest-tested module, raise coverage, fix bugs, improve error handling, and tidy code. Log it in PROGRESS.md.
