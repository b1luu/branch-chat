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

## 2026-10-28 15:48 UTC — B8
- Status: green
- Done: `backend/tests/integration/` with registered `ollama` marker. Session fixture probes `{OLLAMA_BASE_URL}/models` (1s) and skips all `ollama` tests if unreachable. 5 tests: multi-chunk non-empty stream, user+assistant nodes persisted, branch prompt has ancestors and not siblings (via `CapturingOllamaProvider` recording `build_payload`), quote in prompt, merge-back note (skips until D1 adds the route).
- Decisions: tests assert structure only (roles, marker strings we wrote, node parents), never model wording. `LLM_MAX_TOKENS=96` and a "one or two sentences" system prompt keep each test fast. Cloud run: 5 skipped. Dry-ran them against a throwaway fake OpenAI-compatible server (not committed): 4 passed, merge skipped. D1 must make `test_merge_back_produces_nonempty_note` run (route `/api/branches/{leaf_id}/merge`, note on the fork-origin message).
- Next: L1

## 2026-10-28 15:49 UTC — L1
- Status: green
- Done: `scripts/local_test.py` (stdlib only): `git pull --rebase --autostash` → probes Ollama → `pytest -m ollama -q --junitxml` in backend/ with `LLM_PROVIDER=ollama` → appends a LOCAL_TESTS.md entry (SHA, model, pass/total/skipped, first 30 lines per failure) → commits only LOCAL_TESTS.md (`git commit -- LOCAL_TESTS.md`), rebases on origin/main, pushes. `--no-push` writes the report only. 9 unit tests on JUnit parsing, entry format, truncation, not-run note, file appending.
- Decisions: results parsed from JUnit XML (stable, stdlib `xml.etree`) instead of scraping pytest's console. If Ollama is unreachable the entry says `not run — Ollama not reachable at <url>` rather than "0/5 passed", so the cloud agent doesn't treat it as failures. Exit code 1 when any test fails. Dry-ran with `--no-push` against a fake server (4/5 passed, 1 skipped = merge until D1) and with nothing listening (not-run entry); reverted the report both times.
- Ready for Brandon: on the PC, `ollama pull llama3.1:8b`, `cd backend && pip install -e ".[dev]"`, then `python scripts/local_test.py`.
- Next: F1

## 2026-10-28 17:50 UTC — F1
- Status: green
- Done: `frontend/` with Vite 7 + React 19 + TS 5.9 (strict, `noUncheckedIndexedAccess`), Vitest 4 + Testing Library + jsdom, ESLint 9 flat config (typescript-eslint strict, react-hooks, `no-explicit-any` = error). Typed API client `src/api/client.ts` (`listConversations`, `createConversation`, `getTree`, `getPath`, `ApiError` carrying FastAPI's `detail`) with types mirroring `schemas.py`. Dev proxy `/api` + `/healthz` → `BACKEND_URL` (default `http://localhost:8000`). `App` lists conversations with loading/empty/error states. 10 tests (app render states, client URLs/methods/body/encoding/error parsing).
- Decisions: same-origin proxy instead of CORS (SSE passes straight through; prod web container can do the same). Pinned one major behind the newest releases (Vite 7, Vitest 4, ESLint 9, TS 5.9) because typescript-eslint doesn't support TS 7 yet and the newest majors are only weeks old. Shared test helpers in `src/test/fetch.ts` (`mockFetch`, `jsonResponse`, `fetchCall`) for F2+.
- Env note for cloud runs: npm 10.9 (the sandbox's) crashes resolving vitest's optional peer set (`Cannot read properties of null (reading 'edgesOut')`), so the lockfile was generated with `npx -y npm@11 install`. `npm ci` on npm 10 works fine with that lockfile; only regenerate it with npm 11.
- Tidy: untracked the committed `scripts/__pycache__/*.pyc` and added `__pycache__/`, `*.pyc`, `node_modules/`, `frontend/dist/` to `.gitignore`.
- Deps (frontend, new): react, react-dom; dev: vite, @vitejs/plugin-react, typescript, vitest, jsdom, @testing-library/react, @testing-library/jest-dom, eslint, @eslint/js, typescript-eslint, eslint-plugin-react-hooks, eslint-plugin-react-refresh, globals, @types/*.
- Next: F2

## 2026-10-28 18:05 UTC — F2
- Status: green
- Done: `ChatView` (renders root→leaf path minus the system root, quote blockquotes, textarea composer with Enter-to-send / Shift+Enter newline, live streaming bubble, error + retry). `streamReply()` in the API client over a hand-written SSE parser (`src/api/sse.ts`). App now has "New conversation" and opens a conversation at its most recent node. 26 new tests (SSE parser: chunk splits mid-line, CRLF, multi-line data, comments, UTF-8 split; streamReply: event dispatch, HTTP/provider/truncated-stream errors; ChatView: path render, progressive tokens via a test-controlled stream, leaf move, no redundant refetch, blank input, errors; App: open + create flows).
- Decisions: `fetch` + `ReadableStream` instead of `EventSource`, because EventSource can't POST a JSON body. Active leaf lives in `App` (F3's sidebar will set it); after `done`, ChatView appends both persisted nodes locally and remembers the new leaf so it doesn't refetch the path it already has. On a failed reply the server keeps the user turn (B6), but the UI leaves the leaf where it was and restores the draft; a retry creates a sibling branch, which the F3 tree will show. Unknown SSE events are ignored for forward-compat. Added a Testing Library `cleanup()` in setup since Vitest globals are off.
- Deps (frontend dev, new): `@testing-library/user-event` (realistic typing/clicks).
- Next: F3

## 2026-10-28 18:20 UTC — F3
- Status: green
- Done: `TreeSidebar` (ARIA `tree`/`treeitem` with `aria-level`, `aria-selected` on the active leaf, `data-on-path` on the root→leaf path, collapse/expand toggles on forks, click → `onSelect`). Pure helpers in `src/tree.ts` (`childrenIndex`, `pathIds`, `flattenTree`). App tracks the active conversation, renders the sidebar, and clicking a node switches the chat to that branch. 14 new tests (helpers incl. a 20k-deep chain; sidebar render/highlight/select/collapse/refetch/empty/error; App open-with-tree + switch-branch). Added `routeFetch` test helper (mock by `METHOD path`) since App + sidebar + chat now fetch concurrently.
- Decisions: indentation only at forks — a linear chat stays flat and each child of a fork starts a chain one level deeper, so long chats don't march off-screen and branches stand out. Flattening is iterative (no recursion limit). Sidebar refetches `/tree` only when the conversation changes or the active leaf is an id it hasn't seen (i.e. a reply just created it), so clicking around the tree costs no requests. The system root is hidden; if the first turn itself was branched, those starts are indented under it.
- Not verified in a browser this sprint (tests only); worth a manual click-through once S1/docker exists.
- Next: F4

## 2026-10-28 19:50 UTC — F4
- Status: green
- Done: highlighting text inside an assistant message shows an "Ask about this" button on that bubble; clicking it pins the quote above the composer (with "Cancel quote") and focuses the input; the next send POSTs `{content, quote}` to `/api/messages/<quoted message>/reply`, so the branch hangs off that message even when it isn't the leaf. `src/selection.ts` (`selectionInMessage`) decides whether a DOM Selection is a usable quote. 12 new tests (7 selection unit tests via real jsdom Range/Selection, 5 ChatView flows: button visibility, branch from a non-leaf with path truncation + leaf move, cancel, failure keeps quote/path, leaf change drops quote). Test helper `src/test/select.ts`.
- Decisions: a quote must sit inside one message's `.content` (not spanning bubbles, not inside a quote block, not in user turns) so the button always maps to exactly one parent. Button lives inside the bubble rather than a floating popover (no positioning math, accessible by role); `mousedown.preventDefault` keeps the highlight while clicking. Quote is trimmed and capped at 20k chars (backend's `max_length`). While a quote branch streams, the view shows only the path down to the quoted message; on error the original path, quote and draft all stay so retry is one keypress. Changing the active leaf clears any pending quote.
- Next: F5

## 2026-10-28 19:52 UTC — F5
- Status: green
- Done: `BranchNav` above the chat: breadcrumb `Main thread › <side branch> › …` (current crumb `aria-current`) and a back button ("Back to main thread", or "Back to parent branch" when nested). Pure helpers `branchSegments` (split the active path into branches) and `branchTip` (follow oldest children to the end) in `src/tree.ts`. `TreeSidebar` gained an `onTreeChange` callback so App shares its `/tree` fetch with the breadcrumb (no extra request). 14 new tests (7 helper incl. 20k-deep chain, 6 BranchNav, 1 App end-to-end: open on a side branch → breadcrumb → back → main tip).
- Decisions: "main" vs "side" comes from the SPEC definition (a branch is a child of a message that already had one), so the oldest child continues the branch and later siblings start side branches — derived from the tree alone, no extra state or history stack, survives reloads. "Back" and earlier crumbs go to the *tip* of that branch past the fork point (oldest-child chain), so if the main thread grew since you branched you land on its newest turn. Hidden on the main thread and while the tree is refetching for a just-created leaf. Known quirk: a reply that failed mid-stream leaves an orphan user turn (B6), so a retry becomes a later sibling and shows as a side branch; S4 can fix by reusing/cleaning the orphan.
- Next: D1

## 2026-10-28 19:57 UTC — D1
- Status: green
- Done: backend `POST /api/branches/{leaf_id}/merge` (201 → `NoteOut`; 404 unknown leaf, 409 leaf on the main thread, 502 provider error/empty summary, nothing stored on failure) in `app/services/merge.py`; `repository.side_branch_start` + `notes_for`; `build_prompt` inserts each note as a system message right after its fork-origin message for every descendant; `GET /messages/{id}/path` now returns `notes` per message. Frontend: `mergeBranch()` client, "Merge back" button in `BranchNav` (summarize → return to the parent branch's tip), notes rendered as "Merged from a side branch" asides under their message. 14 backend + 9 frontend tests.
- Decisions: fork origin uses the same oldest-child rule as F5's breadcrumb, and merges the *innermost* side branch (nested branches merge into their parent branch, not straight to main). Summarizer gets one system + one user turn holding a transcript (fork origin + branch turns, each clipped to 4k chars) so it works with Anthropic's strict alternation; `MERGE_MAX_TOKENS` (default 300) caps it. Merge isn't streamed (short, only useful once complete). A note is skipped in the context of the branch it summarizes (its source leaf is on the path) to avoid duplicating turns the model already sees; multiple merges just add notes. Notes are system messages mid-prompt: fine for Ollama/OpenAI; Anthropic's payload builder lifts system turns into top-level `system`, so there they lose position (still present) — revisit with D4. No migration: the `notes` table has existed since B2.
- Test changes (not weakened): `test_path_returns_root_to_node_without_siblings` pinned the exact path fields; added `notes` and asserted it's empty. B8's `test_merge_back_produces_nonempty_note` skip-guard inspected `app.routes`, which on FastAPI 0.162 hides included routers (`_IncludedRouter`), so it would have skipped forever; removed the guard now that the route exists. Dry-ran all 5 `ollama` tests against a throwaway fake OpenAI-compatible server (not committed): 5/5 pass.
- Next: D2

## 2026-10-28 21:48 UTC — D2
- Status: green
- Done: `app/services/budget.py`: token estimate (ceil(chars/4) + 4/message), `fit_to_budget()` applied in `stream_reply` before the provider call. Over `CONTEXT_TOKEN_BUDGET` (default 60k), the oldest ancestors between the root system prompt and a cut point become one system message `Summary of the earlier part…` produced by the provider (`CONTEXT_SUMMARY_MAX_TOKENS`, default 500); newest turns stay verbatim. Nothing is persisted to the tree. 20 new tests (estimates, env, cut selection/stability, summary placement + transcript, cache hit/miss, summarizer failure/empty, bounded transcript, reply endpoint with a tiny budget/under budget/summarizer down/provider error).
- Decisions: char-based estimate (no tokenizer dep, provider-agnostic, slightly conservative). Cut point rounded up to multiples of 8 messages and moved forward to a user turn (Anthropic needs user-first after system), so the summarized prefix stays identical for several replies; summaries are cached in-process (LRU 256) by provider + hash of the condensed messages, so most replies over budget cost no extra LLM call, and sibling branches with the same old ancestors share a summary. Summarizer failure/empty → drop old turns with an "omitted" marker instead of failing the reply. Summarizer input clipped (4k chars/turn, budget×4 chars total, keeping the most recent). Newest user turn always kept even if it alone exceeds the budget. Cache is per-process (fine for one API container; Redis/DB if it ever scales out).
- Config: `.env.example` gains `CONTEXT_TOKEN_BUDGET`, `CONTEXT_SUMMARY_MAX_TOKENS`. No new deps.
- Next: D3

## 2026-10-28 21:58 UTC — D3
- Status: green
- Done: `app/api/ratelimit.py`: per-IP `TokenBucketLimiter` (burst capacity + continuous refill, injectable clock) and a pure-ASGI `RateLimitMiddleware` that limits only `POST …/reply` and `POST …/merge` (one shared bucket per IP), answering `429` + `Retry-After` (whole seconds until a token) + JSON `detail`. Wired in `create_app` from `RATE_LIMIT_ENABLED|PER_MINUTE|BURST|TRUST_PROXY` (defaults on, 20/min, burst 10, no proxy trust). 21 new tests (bucket burst/refill/cap/per-client/zero rate/pruning, path matching, client key, env config, endpoint 429 + Retry-After countdown + nothing persisted, reads unlimited, merge limited, proxy-separated clients, disabled, on by default).
- Decisions: pure ASGI instead of `BaseHTTPMiddleware` so SSE bodies pass through untouched, and rejected requests never reach the route (no orphan user node). In-memory buckets (single API container); idle full buckets pruned past 10k clients. Behind a proxy, `RATE_LIMIT_TRUST_PROXY=true` keys on the *last* X-Forwarded-For hop (what our proxy saw; the first is client-controlled) — S1 should set it on the api container since the web proxy makes every request look like one IP. Frontend already shows the 429 `detail` via `ApiError`; nicer UX (countdown) left for S4.
- Tests: `tests/conftest.py` gained an autouse fixture setting `RATE_LIMIT_ENABLED=false` so existing tests that fire many replies from one TestClient IP aren't throttled; limiter tests enable it explicitly (incl. one asserting it's on by default from env). Gotcha fixed along the way: a `__len__` on the limiter made an empty limiter falsy, so `limiter or default` silently discarded the test's fake clock — replaced with a `tracked_clients` property and an `is not None` check.
- Config: `.env.example` gains the `RATE_LIMIT_*` vars. No new deps.
- Next: D4

## 2026-10-28 22:08 UTC — D4
- Status: green
- Done: `AnthropicProvider.build_payload` adds up to 3 `cache_control: ephemeral` breakpoints: end of `system`, end of the ancestor prefix (turn before the newest user turn), and the newest user turn. `ANTHROPIC_PROMPT_CACHE` (default true) / `prompt_cache=` toggles it. `message_start` usage (input / cache_write / cache_read tokens) is logged at INFO so hits are visible. 11 new payload tests (exact cached payload, ≤4 breakpoints on long paths, single turn, disable flag, sibling branches share a byte-identical prefix, next turn extends the previous prefix, merge note inlined without touching `system`, D2 summary stays in cached `system`, a real DB path with a note yields valid alternation, cached payload is what goes over the wire + usage log).
- Decisions: the breakpoint on the ancestor end is what makes the tree cheap — every sibling branch replying to the same message is an exact cache hit; the one on the newest turn covers the linear "next reply" case. Only *leading* system messages go into top-level `system` now; mid-path system messages (D1 merge notes) are inlined at their position as `<system_note>` user-role text. Lifting them (the old behavior) changed the first bytes of every later prompt, busting the cache, and lost their position — the D1 entry flagged this. Short prefixes under the model's minimum cacheable length just don't cache; markers are harmless.
- Test changes (not weakened): `test_to_anthropic_messages_splits_system_and_merges_same_role` pinned the old lift-all-system behavior; renamed and now asserts the note stays in place. `test_anthropic_streams_text_deltas_with_correct_request` pins the exact uncached payload, so it now passes `prompt_cache=False`; the cached shape is pinned exactly in the new `test_prompt_caching.py`.
- Config: `.env.example` gains `ANTHROPIC_PROMPT_CACHE`. No new deps.
- Next: S1

## 2026-10-29 16:20 UTC — S1
- Status: green
- Done: `backend/Dockerfile` (python:3.12-slim, non-root, healthcheck on `/healthz`) whose entrypoint runs `python -m app.db.migrate` (wait for DB with retry/backoff → `alembic upgrade head`, idempotent) then uvicorn. `frontend/Dockerfile` (node build → nginx) with `frontend/docker/nginx.conf` serving the SPA and proxying `/api` + `/healthz` to the API with `proxy_buffering off` for SSE. `docker-compose.yml`: db (postgres:16-alpine, healthcheck, volume) → api (waits for healthy db) → web on :8080 (waits for healthy api). `scripts/smoke_test.py` (stdlib): health, create conversation, streamed reply persisted, sibling branch path excludes the first branch, tree has 5 nodes; `--compose` does up --build / down -v. 13 new tests (migrate: schema + idempotent restart, retry/backoff, DB appearing mid-wait, exit 1 when never up; smoke script run against the in-process app via a TestClient transport, SSE parsing, error/no-token/HTTP-error/sibling-leak detection).
- Verified for real in the sandbox: built both images, `docker compose up`, migrations ran on Postgres, smoke test passed through nginx (12 SSE token events streamed), SPA served with history fallback, `restart api` re-ran migrations as a no-op.
- Decisions: migrate in the entrypoint rather than a separate one-shot service (simpler, restart-safe since upgrade is idempotent); the retry loop covers Postgres's brief post-healthcheck restart. Browser traffic goes through nginx → one origin, no CORS (same as the Vite dev proxy). api sets `RATE_LIMIT_TRUST_PROXY=true` (D3 note) and publishes :8000/:5432 on 127.0.0.1 only, so nobody off-box can spoof X-Forwarded-For past the web proxy. Ollama on the host is reached via `host.docker.internal` (`extra_hosts: host-gateway`, works on Linux too); override with `OLLAMA_BASE_URL_DOCKER`. `.env` is optional (`required: false`); provider defaults to mock. Alembic's `fileConfig` is skipped in the migrate module so it doesn't disable existing loggers.
- Env note for cloud runs: the sandbox's Docker daemon isn't running by default (`nohup dockerd &` works as root); Docker Hub returns 429, so base images were pulled from `mirror.gcr.io/library/*` and retagged; containers don't trust the egress proxy CA, so the images were built from throwaway copies of the Dockerfiles with the CA + proxy injected (not committed). The committed Dockerfiles are unchanged standard ones.
- No new deps.
- Next: S2

## 2026-10-29 16:35 UTC — S2
- Status: green (CI itself first runs on this push)
- Done: `.github/workflows/ci.yml` on push to main + PRs: `backend` (Python 3.12, `pip install -e ".[dev]"`, `ruff check .`, `pytest -q` with a Postgres 16 service + `TEST_POSTGRES_URL`, so the `any_db` CTE tests run on Postgres too), `frontend` (Node 22, `npm ci`, lint, `vitest --run`, build), and `docker` (after both pass: `python3 scripts/smoke_test.py --compose`, building the real images and smoke-testing the stack). 12 new tests in `test_ci_workflow.py` (triggers, every check present, mock LLM + Postgres env, every command in CLAUDE.md's full check is also in CI, no secrets referenced).
- Verified locally: backend suite against a real Postgres 16 container (161 passed, only the 5 `ollama` tests skipped); workflow passes `actionlint` and parses as YAML.
- Decisions: plain-text workflow tests instead of adding PyYAML; the CLAUDE.md-sync test reads the sprint-loop commands straight from CLAUDE.md so CI and the local check can't drift. `concurrency` cancels superseded runs; `permissions: contents: read`; pip/npm caches keyed on pyproject/lockfile. `ollama` tests auto-skip in CI (no Ollama reachable), as designed in B8.
- No new deps (actionlint was used ad hoc, not committed).
- Next: S3

## 2026-10-29 16:50 UTC — S3
- Status: green (S2's first CI run on GitHub was green on all 3 jobs, incl. the docker smoke test)
- Done: README rewritten: pitch, CI badge, features, Mermaid architecture diagram (browser → nginx → rate limit → routes → services → CTE repo/Postgres + provider → Ollama/Anthropic), reply data-flow, Docker quick start (mock / Ollama / Haiku), local dev, test tiers table, config table, design decisions (tree model, recursive CTE with the SQL, SSE, prompt caching, context budget, merge back, rate limiting), layout. 14 new tests in `test_readme.py` (diagram present, required sections, design topics, screenshot placeholder, relative links resolve, every env var in the config table exists in `.env.example`).
- Verified: Mermaid block renders with `mmdc`.
- Finding: I brought the stack up, built a real branched conversation (quote branch + merge back) and screenshotted it with Playwright. Everything works (tree, breadcrumb, Merge back, merged note, quote), but **the frontend has no CSS at all**: default browser buttons and bullet lists. Not a README screenshot yet, so the README keeps a placeholder (as the spec allows) with a TODO. Suggest a backlog item for basic styling (layout: sidebar + chat pane, message bubbles, tree indentation); not added myself since it isn't in the backlog.
- Decisions: Ollama in Docker needs `OLLAMA_HOST=0.0.0.0` on Linux so the container can reach it via host-gateway; documented.
- No new deps.
- Next: S4

## 2026-10-29 17:52 UTC — S4
- Status: green
- Done: **Backend validation**: reply `content`, conversation `title` and `system_prompt` reject whitespace-only (422 "must not be blank"); titles are trimmed/collapsed to one line; a blank `quote` becomes `null`; content is otherwise kept verbatim. **Retry without orphans** (the F5/B6 quirk): re-sending the exact same text + quote under the same parent reuses the childless user turn a failed reply left behind (`repository.childless_user_turn`), so a retry no longer shows up as a fake side branch. **Frontend**: shared `ErrorNotice` (alert + "Try again") for the conversation list, tree (initial load and failed refresh, which now keeps the last tree instead of failing silently) and chat path; API client formats FastAPI 422 lists into one line (`content: must not be blank`), adds "Try again in N s." + `retryAfter` to 429s, and maps network failures to "Can't reach the server…" (aborts rethrown untouched); "New conversation" shows "Creating…" and opening shows "Opening conversation…" (buttons disabled, no double-fire); action errors name what failed and can be dismissed; composer shows a counter near the 50k cap and blocks over-long sends with `aria-invalid`; clearer empty state.
- Tests: 19 backend (`test_hardening.py`: blank/over-limit/missing fields, verbatim whitespace, blank quote, 404s, orphan reuse on retry vs. different text/quote/parent/already-answered) + 22 frontend (`src/hardening.test.tsx`: 422/429/network/abort in the client, ErrorNotice, list/tree/path retry, refresh failure keeps tree, create/open loading + double-click guard, rate-limited reply keeps draft, whitespace and length validation, streaming busy state). No existing tests changed.
- Decisions: orphan reuse is exact-match only (any edit is a new turn), and an answered turn is never reused, so asking the same thing twice on purpose still branches. Edge: two identical sends racing before either has an answer share one user node (two assistant children) — the UI already blocks sending while streaming. Retry is driven by an attempt counter in the fetch effect's deps; state resets happen in the click handler, not the effect (react-hooks `set-state-in-effect` lint). Still no CSS (S3 finding); the new elements carry classes (`error-notice`, `char-count`, `empty`) ready for a styling pass.
- No new deps.
- Next: backlog complete. Suggest adding a styling item (layout, bubbles, tree indentation) so the README can get a real screenshot; otherwise per BACKLOG, coverage/tidy passes.

## 2026-10-29 18:20 UTC — Q1, Q2 (post-backlog bug/tidy pass)
- Status: green
- Done: **Q1** frontend `parseSSE`: a CRLF split across network chunks (`…\r` | `\n…`) inside an event was normalized to two line breaks, a fake blank line that cut the event in half and dropped its `event:` name (a `token` event arrived as `message`). Now a trailing `\r` is held back until the next chunk; the final flush also normalizes CRs. 2 new tests (split CRLF mid-event / across 3 chunks, bare-CR line endings incl. at end of stream). The old CRLF test only split at the very end, where the bug was harmless. **Q2** `alembic/env.py` never disposed its engine, so every `alembic upgrade` (container entrypoint + migration tests) left a SQLite/Postgres connection open until GC — the source of the 4 `ResourceWarning: unclosed database` warnings in the test run. Wrapped in `try/finally: engine.dispose()`; new `test_no_leaked_connections.py` (fails before, passes after); suite now runs with 0 warnings.
- Decisions: backend coverage is 99% and frontend ~97% statements (measured ad hoc with pytest-cov / @vitest/coverage-v8, not added as deps), so this pass hunted for real bugs instead of padding coverage. Our backend emits `\n` only, so Q1 bites only behind a proxy that rewrites line endings — cheap to make robust. `ruff format --check` flags 2 pre-existing files (`app/services/chat.py` …); not part of the required check, left alone.
- No new deps.
- Next: backlog complete. Still suggest a styling backlog item (S3 finding: no CSS) so the README can get a real screenshot.

## 2026-10-29 21:55 UTC — Q3 (post-backlog bug pass)
- Status: green
- Done: **Q3** ChatView: switching branch or conversation while a reply was streaming kept the old branch's pending turn on screen (appended to the new path), kept "Sending…" blocking the composer, and on `done` appended the old reply to the *new* path and called `onLeafChange` with a node from the other branch/conversation (sidebar on conversation B, chat leaf in A). Now an externally driven leaf change *detaches* the in-flight send (`activeSend` ref token): its tokens, result and errors are ignored and the view resets, but the request is **not** aborted, so the server still persists the reply the user asked for. 4 new tests (new path shown, background finish doesn't hijack leaf/path and isn't aborted, old failure/draft not surfaced, can send on the new branch while the old stream runs); all 4 failed before the fix.
- Decisions: detach rather than abort — aborting would throw away a reply mid-generation (and leave a childless user turn that only S4's retry-reuse cleans up). Known gap, left as is: the sidebar tree doesn't learn about the background reply until its next refetch (any new node or reopening the conversation), so it can briefly look missing.
- Checks: backend ruff + pytest (201 passed, 11 skipped), frontend lint + 113 tests + build.
- No new deps.
- Next: backlog complete. Still suggest a styling backlog item (S3 finding: no CSS) so the README can get a real screenshot.

## 2026-10-30 15:49 UTC — Q4 (post-backlog bug pass)
- Status: green
- Done: **Q4** closes the gap Q3 left: a reply that finishes after the user navigated away (detached send) never became the active leaf, so the sidebar tree (and the breadcrumb, which shares its nodes) didn't show it until some unrelated refetch. ChatView gains `onReplyPersisted(assistant)`, fired for every reply the server persisted, attached or detached (not for failures). App keeps a per-conversation reply counter and passes it to TreeSidebar as `refreshKey`; TreeSidebar refetches when the key differs from the one its tree was fetched with. 5 new tests: ChatView callback for detached + attached replies and none on failure, TreeSidebar refetch on key bump only (same key → no fetch, selection unchanged), App end-to-end (send on main thread → click a side branch → reply lands → tree shows it, user stays on the side branch, exactly one extra `/tree` fetch), and a guard that a background reply in conversation A doesn't refetch B's tree. The first 4 failed before the fix.
- Decisions: counter keyed by `conversation_id` from the persisted message, so a stale closure from when the send started can still update it safely via the functional setState (no refs written during render). A counter rather than pushing the node into the tree locally: `/tree` stays the single source of truth and also picks up the user node.
- Checks: backend ruff + pytest (201 passed, 11 skipped), frontend lint + 118 tests + build.
- No new deps.
- Next: backlog complete. Still suggest a styling backlog item (S3 finding: no CSS) so the README can get a real screenshot.

## 2026-10-30 17:55 UTC — Q5 (post-backlog bug pass)
- Status: green
- Done: **Q5** `stream_reply`: when the provider finished without error but produced no text (nothing, `""`, or only whitespace — e.g. a local model hitting a stop token immediately), a blank assistant node was persisted and `done` sent. That left a dead empty bubble, blocked S4's retry-reuse (the user turn was no longer childless), and, worse, with `LLM_PROVIDER=anthropic` every later reply on that branch would 400, since the Messages API rejects empty assistant content. Now a blank answer is an `error` event ("LLM returned an empty reply"), nothing is persisted, and re-sending reuses the user turn. 4 new tests in `test_hardening.py` (no chunks / one empty chunk / whitespace chunks → `error`, no assistant row, user turn kept; retry after an empty reply reuses the turn and gets a real answer); all failed before the fix.
- Decisions: reject at the source (don't persist) rather than having the Anthropic provider skip empty turns, so the tree never contains a node the UI can't show. The existing ChatView error path ("Try again", draft kept) handles the new event with no frontend change.
- Noted, not changed: merging the same leaf twice (or a double-submit) stores two identical notes, both injected into descendants' context. Making merge idempotent per `source_leaf_id` is a design call (what should re-merging a branch that grew do?) — suggest a backlog item.
- Checks: backend ruff + pytest (205 passed, 11 skipped), frontend lint + 118 tests + build.
- No new deps.
- Next: backlog complete. Suggested backlog items: basic styling (S3 finding) and idempotent merge-back.

## 2026-10-30 19:55 UTC — Q6 (post-backlog bug pass)
- Status: green
- Done: **Q6** merge back is idempotent per leaf (the duplicate-note issue flagged in Q5). Merging a leaf that already has a note returns that note with `200` and no LLM call (was: a second identical note, both injected into every descendant's context, and a second paid summary). New unique index `uq_notes_source_leaf_id` (migration **0002**, which first deletes existing exact duplicates keeping the oldest) closes the double-submit race: if a concurrent merge of the same leaf commits while ours is summarizing, our insert hits `IntegrityError`, we roll back and return the winner's note. NULL `source_leaf_id` (leaf deleted) stays unlimited. 8 new tests in `test_merge_idempotent.py` (repeat → 200 same note + one LLM call; one note in path and in the next reply's prompt; distinct leaves still get their own notes; retry after a 502 creates; simulated race returns the winner; DB rejects a duplicate; NULL leaf ids allowed; migration dedupes keeping the oldest, adds the unique index, downgrades). 4 failed before the fix (the others pin unchanged behavior).
- Decisions: deliberately **not** changing what re-merging a *grown* branch does (deeper leaf → second note next to the first): `test_path_includes_notes_on_their_message` pins that D1 behavior, and replacing the shallower note is a product call. Suggest a backlog item: "re-merge of a grown branch replaces the earlier note from the same branch". Idempotency lives in the service + DB, so the frontend needed no change (`request()` accepts any 2xx). README/SPEC note the 200.
- Verified: full backend suite also on a throwaway Postgres 16 (219 passed, ollama skipped) and migration 0002 on Postgres with duplicate rows (oldest kept, NULLs untouched, down/up clean).
- No new deps.
- Next: Q7 (SQLite returns naive datetimes, so API timestamps lose their `Z` once read back from the DB).

## 2026-10-30 20:10 UTC — Q7 (post-backlog bug pass)
- Status: green
- Done: **Q7** SQLite (the default `DATABASE_URL` for local dev) has no time zone type, so every timestamp *read back* from the DB was naive and the API emitted `created_at` without `Z`, while freshly created rows had it: the same note serialized differently on first merge vs. repeat (found while writing Q6's tests), and a browser parses a naive ISO string as local time. Also, an aware non-UTC value was stored as its wall-clock text on SQLite, so ordering could follow wall clock instead of real time. New `UTCDateTime` type decorator (`app/db/models.py`) over `DateTime(timezone=True)`: normalizes to UTC on write, tags naive values as UTC on read. Same underlying column type, so no migration (the drift test still passes). 7 new tests in `test_utc_timestamps.py` (all API timestamps read from the DB are UTC; repeat merge JSON identical; `done` event timestamp equals the reloaded path's; round-trip of a +02:00 value is the same instant in UTC; tree order follows the instant, not wall clock — the last two on SQLite and Postgres). All 5 SQLite tests failed before the fix.
- Decisions: fix at the type level rather than in the Pydantic schemas, so every ORM read (services, ordering, budget) sees aware UTC, not just the JSON. Postgres already behaved; verified the suite on a throwaway Postgres 16 (226 passed, ollama skipped).
- No new deps.
- Next: backlog complete. Suggested backlog items: basic styling (S3 finding), and re-merge of a grown branch replacing the earlier note (Q6 decision).

## 2026-10-30 21:50 UTC — Q8 (post-backlog bug pass)
- Status: green
- Done: **Q8** clicking a question ("You:" node) in the tree makes it the active leaf, and sending from there created a user turn *under* a user turn: the prompt then had two user turns in a row and the selected question never got its own answer (an orphan from a failed reply could be built on the same way). **Backend**: `create_user_node` now rejects a user parent with `409` ("…is a user turn; reply to an assistant or system message"), nothing persisted. **Frontend**: with a question as the leaf, ChatView sends *in its place* (under the question's parent, a sibling branch) and shows a hint above the composer (`composer-hint`, linked via `aria-describedby`); re-sending a failed question's exact text that way hits S4's retry-reuse instead of branching. Quote branches are unchanged. 4 backend tests (`test_reply_parent_role.py`: 409 + nothing persisted, 409 for an unanswered orphan, sibling path alternates cleanly and excludes the replaced question, assistant/system parents still fine) + 4 frontend tests (ChatView: sends to the parent, replaced question hidden while streaming, quote still targets the quoted message, no hint on an assistant leaf). 2 + 2 failed before the fix.
- Decisions: "ask in place of" over "answer this question" for a selected user leaf: it reuses the existing reply endpoint, and the retry case still works through orphan reuse. 409 (not 422) since the body is valid and it's the tree state that doesn't fit. SPEC endpoint line updated.
- Checks: backend ruff + pytest (222 passed, 13 skipped), frontend lint + 122 tests + build.
- No new deps.
- Next: backlog complete. Suggested backlog items: basic styling (S3 finding), and re-merge of a grown branch replacing the earlier note (Q6 decision).

## 2026-10-31 UTC — Q9 (post-backlog bug pass)
- Status: green
- Done: **Q9** frontend `childrenIndex` sorted siblings with `created_at.localeCompare`, but the API drops the fraction when it is exactly zero (`…:42Z`) and sends 6 digits otherwise (`…:42.500000Z`); since `"."` < `"Z"`, a child created at `42.5` sorted *before* one created at `42.0`. The oldest child decides which branch is the main thread, so the sidebar indentation, breadcrumb, "Back to main thread" target (`branchTip`) could disagree with the backend (`side_branch_start`, `ORDER BY created_at, id`), e.g. "Merge back" offered on what the server considers the main thread (409). New `compareCreated` in `tree.ts` compares whole-second instants, then the fraction padded to fixed width (keeps microseconds, which `Date.parse` would round to ms), then id, matching the server's order. 5 new tests in `tree.test.ts` (whole-second vs fractional, microsecond precision, offsets compared as instants, id tiebreak, branchSegments/branchTip pick the right main thread); the ordering ones failed before the fix.
- Decisions: fixed the comparison on the client rather than forcing a fixed-width timestamp format in every API schema: the client shouldn't depend on string formatting of timestamps, and it keeps the API output unchanged. Hit rate is low (the fraction must be exactly 0 µs) but the effect is a wrong main thread, so worth closing.
- Checks: backend ruff + pytest (222 passed, 13 skipped), frontend lint + 127 tests + build.
- No new deps.
- Next: backlog complete. Suggested backlog items still open: basic styling (S3 finding), and re-merge of a grown branch replacing the earlier note (Q6 decision).

## 2026-11-01 UTC — Q10 (bug found on Brandon's Windows PC)
- Status: green
- Done: **Q10** `test_tree_returns_all_branches_parents_first` failed on Brandon's Windows machine (first local run, Python 3.12): Windows' clock ticks in ~15 ms steps, so messages created back to back can share `created_at`, and the `ORDER BY created_at, id` tiebreak (random hex ids) put a reply before its parent, breaking `/tree`'s parents-first contract. `get_tree` now passes rows through new `repository.parents_first`: a stable O(n) topological pass that keeps time order but holds a node back until its parent is out. 2 new tests in `test_api_conversations.py` (all timestamps pinned to one instant with ids sorted against the tree; pure-function order check); both failed before the fix. Linux never reproduces the original failure (µs clock), hence the pinned-timestamp test.
- Decisions: fixed in Python after the query rather than with a depth-ordered recursive CTE: same single query, keeps time order between unrelated branches, and sibling order (which decides the main thread for merge/breadcrumb, Q9) is untouched.
- Checks: backend ruff + pytest (224 passed, 13 skipped), frontend lint + 127 tests + build.
- No new deps.
- Next: backlog complete. Suggested: basic styling, re-merge replacing the earlier note, and a backend lock file (pip resolves `>=` ranges to newer versions on each machine).

## 2026-11-01 23:58 UTC — U1
- Status: green
- Done: **U1** assistant replies and merge notes render as Markdown via new `components/Markdown.tsx` (`react-markdown` + `remark-gfm`): headings, emphasis, lists, inline code, fenced code (`Markdown.css`: monospace, `white-space: pre`, horizontal scroll). User turns stay plain text with `white-space: pre-wrap` (`.content.plain`). Links open in a new tab with `rel="noopener noreferrer"`. 7 new tests in `Markdown.test.tsx`: elements render (whitespace kept in code), `<script>`/`<img onerror>` stay inert text, link attrs + `javascript:` URL dropped, notes render Markdown, user newlines kept and `**` not parsed, partial fence mid-stream then the closed block, selection across `<strong>`/`<code>` quotes the visible text and is sent as the quote.
- Decisions: no `rehype-raw`, so raw HTML is never parsed into elements (react-markdown shows it as text) and no `dangerouslySetInnerHTML` anywhere; default `urlTransform` strips unsafe URL schemes. The whole partial text is re-parsed on each token: simple, fine at chat sizes, and Markdown has no error state, so an unclosed fence just renders as an open code block. Selection code needed no change: `.content` still wraps the rendered tree and `Selection.toString()` yields the visible text. Notes keep their own wrapper (`.note-body`) outside `.content`, so they still never offer "Ask about this".
- Checks: backend ruff + pytest (224 passed, 13 skipped), frontend lint + 134 tests + build.
- New deps: `react-markdown` ^10.1, `remark-gfm` ^4.0 (bundle 405 kB / 126 kB gzip).
- Next: U2 (basic styling + real screenshot).

## 2026-11-02 00:20 UTC — U2
- Status: green
- Done: **U2** new `frontend/src/styles.css` (plain CSS, imported in `main.tsx`): color tokens on `:root` with a `prefers-color-scheme: dark` set; grid layout (header, sidebar left, chat right, stacks under 800px); messages scroll inside the chat pane with the composer pinned at the bottom; user bubbles right/tinted, assistant left; quotes as blockquotes; merge notes in a pinned-note look; tree without bullets, depth indentation, active path/node highlighted, side branches marked with an orange rail; `:focus-visible` rings; existing state classes (`error-notice`, `empty`, `char-count`, `composer-hint`) styled. `flattenTree` rows gain `side` (off the main thread, same rule as `branchSegments`), rendered as `.side-branch`. New `scripts/screenshot.py` generates `docs/screenshot.png` (now in the README in place of the placeholder).
- Tests: `styles.test.ts` (entry point imports the stylesheet; 20 key selectors styled; dark theme, narrow layout, focus rings; tree has no bullets), `tree.test.ts` side-branch rows (incl. a fork inside a side branch and multiple first turns), `TreeSidebar.test.tsx` side rows get `.side-branch`. No layout snapshots.
- Decisions: stylesheet test reads the CSS as text with `node:fs` (Vitest stubs CSS imports to empty strings, even `?raw`), with a file-local `/// <reference types="node" />` so the app tsconfig stays browser-only. Screenshot script serves `frontend/dist` and the API from one in-process FastAPI app on a temp SQLite DB, with a scripted mock provider (canned Markdown replies in call order) so the shot shows real formatting; it builds the conversation through the real API (2 main turns, a quote branch, a merge back). Dev-only: needs Playwright for Python, not added to project deps.
- Noticed, not changed: tree previews show raw Markdown (`**…**`, backticks) since `preview` is the stored text cut to length. Suggest a small item: strip Markdown syntax from previews (backend `preview` or frontend label).
- Checks: backend ruff + pytest (224 passed, 13 skipped), frontend lint + 159 tests + build.
- No new deps.
- Next: backlog complete. Suggested: strip Markdown from tree previews, re-merge of a grown branch replacing the earlier note (Q6), backend lock file (Q10).

## 2026-11-02 UTC — Q11 (post-backlog bug pass)
- Status: green
- Done: **Q11** tree previews (sidebar labels, breadcrumb) showed raw Markdown from assistant replies (`**…**`, backticks, `##`, list dashes), the U2 finding. New `app/services/preview.py`: `markdown_to_text` strips fences (code kept), rules, table separators, headings, quotes, list/task markers, images/links (text kept), inline code, bold/italic/strike; `make_preview` replaces `routes._preview` and strips *before* truncating, so the 80-char cap counts visible text and never cuts a `**` pair. User turns are left verbatim (they render as plain text, and `**kwargs` is what they typed). 19 new tests in `test_preview.py` (each syntax, code contents not treated as emphasis, snake_case / `2 * 3` survive, unclosed marker left alone, strip-then-truncate, user turns untouched, `/tree` end to end); the end-to-end and truncation tests failed before the fix. SPEC `/tree` line updated.
- Decisions: backend rather than frontend, so every consumer of `preview` (tree, breadcrumb) gets plain text and the client stays a dumb label. Small regex stripper instead of a Markdown library: a preview only has to be readable, not exact, and it avoids a new backend dep. Line-level patterns use `[ \t]` instead of `\s` so they can't eat across lines (caught by a test).
- Checks: backend ruff + pytest (243 passed, 13 skipped), frontend lint + 159 tests + build.
- No new deps.
- Next: backlog complete. Still suggested: re-merge of a grown branch replacing the earlier note (Q6, product call), backend lock file (Q10).

## 2026-11-02 UTC — Q12 (post-backlog: backend lock file)
- Status: green
- Done: **Q12** `pyproject.toml` only has ranges, so CI, the Docker image and Brandon's PC each resolved whatever was newest that day (suggested since Q10). New `backend/requirements.lock` (runtime) and `backend/requirements-dev.lock` (+ ruff/pytest/respx), generated with `uv pip compile --universal --python-version 3.12` (regenerate command in each file's header). Docker installs `requirements.lock` then the project with `--no-deps`; CI installs `requirements-dev.lock` + `pip install --no-deps -e .` (pip cache keyed on the lock); README setup updated (incl. Windows venv activate). Also set `asyncio_default_fixture_loop_scope = "function"` in pytest config to silence a pytest-asyncio deprecation warning that newer pins surfaced.
- Tests: new `test_lockfile.py` (26 tests, no network): only exact `==` pins; every runtime + dev dep in pyproject is locked at a version its range allows; extras (`psycopg-binary`, uvicorn `standard` packages) locked; dev lock pins runtime packages at the same versions as the runtime lock; locks are universal (uvloop marked not-win32, colorama win32-only); headers carry the regen command; Dockerfile and README use the locks. `test_ci_workflow.py` now expects the lock-based install. Sanity: bumping a pin outside its range fails two tests.
- Decisions: `uv pip compile` output as plain requirements files rather than switching the project to uv/poetry: install stays plain `pip install -r`, works on Windows with stdlib Python, and uv is only needed to *regenerate* (dev tool, not a project dep). `--universal` so one lock covers Linux CI/Docker and Brandon's Windows PC. Kept `pyproject.toml` ranges as the source of truth; the lock test catches drift.
- Verified: fresh Python 3.12 venv from `requirements-dev.lock`: ruff + full suite green (269 passed, 13 skipped) on the newly pinned versions (FastAPI 0.162, Starlette 1.10, SQLAlchemy 2.1, pytest 9.2); Dockerfile install step reproduced with plain pip (`pip check` clean). No Docker daemon in the sandbox, so the image build itself is left to CI's docker job. Frontend lint + 159 tests + build.
- Noticed, not changed: Starlette 1.10 warns that `TestClient` over `httpx` is deprecated in favor of `httpx2`; upstream, tests unaffected. Worth a look when it becomes an error.
- No new deps (uv is a regen-only dev tool).
- Local agent: next local run should reinstall with `pip install -r backend/requirements-dev.lock` so the PC tests the pinned versions.
- Next: backlog complete. Still suggested: re-merge of a grown branch replacing the earlier note (Q6, product call).

## 2026-11-03 18:55 UTC — Q13 (post-backlog bug pass: unpaired surrogates)
- Status: green
- Done: **Q13** a request string holding half an emoji (JSON `"\ud83d"` with no low surrogate) got a **500** instead of a 422 on every write endpoint (reply content/quote, conversation title/system prompt). Pydantic rejects it correctly, but FastAPI's default 422 handler echoes the bad `input` back and crashes UTF-8 encoding it. New `app/api/validation.py` replaces that handler: same `{"detail": [...]}` body, with lone surrogates in it replaced by U+FFFD. The frontend could produce such a string itself: the quote was cut with `.slice(0, MAX_QUOTE_CHARS)` and breadcrumb labels with `.slice(0, 31)`, both in UTF-16 units. New `src/text.ts` (`codePointLength`, `truncateCodePoints`) is now used for the quote cap, breadcrumb labels and the composer's character counter/limit, which now count code points like the backend's `max_length` (50,000 emoji used to be blocked as "100,000 characters").
- Tests: backend `test_unicode_input.py` (16: lone high/low surrogate in content and quote → 422 + nothing persisted, title/system_prompt → 422, escaped *valid* pair still works, limits count code points, ordinary 422 shape unchanged, `scrub_surrogates` cases); 8 failed before the fix. Frontend `text.test.ts` (count, truncate never leaves a lone surrogate), ChatView quote cap at an emoji boundary, composer counter with emoji, BranchNav label cut at an emoji + `(empty)` label; the 3 component tests failed before the fix.
- Decisions: first tried a schema validator rejecting unencodable strings, but pydantic's JSON parser already rejects them (`string_unicode`), so the bug was only in rendering the 422; fixed there, once, for every endpoint. Kept the default 422 shape so the frontend's error display is unchanged.
- Noticed, not changed: assistant text from a provider is not checked for lone surrogates before it's stored (would need a provider that streams JSON-escaped half pairs; none of ours do).
- Checks: backend ruff + pytest (285 passed, 13 skipped), frontend lint + 167 tests + build.
- No new deps.
- Next: backlog complete. Still suggested: re-merge of a grown branch replacing the earlier note (Q6, product call).

## 2026-11-03 19:05 UTC — Q14 (post-backlog: SSE parser edge cases)
- Status: green
- Done: **Q14** `api/sse.ts` had untested branches in field parsing. 2 new tests in `sse.test.ts`: no space after the colon, only one leading space stripped, a bare `data` line (no colon) is an empty value, later colons stay in the value, `id`/`retry`/unknown fields ignored. All passed first time: the parser already followed the SSE spec; this pins it.
- Checks: backend ruff + pytest (285 passed, 13 skipped), frontend lint + 169 tests + build.
- No new deps.
- Next: backlog complete. Still suggested: re-merge of a grown branch replacing the earlier note (Q6, product call).

## 2026-11-03 22:50 UTC — Q15 (post-backlog bug pass: resume point + create error)
- Status: green
- Done: **Q15** opening a conversation resumes on its newest node, but `App.latestNodeId` compared `created_at` as strings, the exact trap `tree.ts` documents: the API omits a zero fraction (`…:05Z`) and `"."` sorts before `"Z"`, so a node at `…:05.5Z` lost to an older one at `…:05Z` and the app resumed on the wrong branch. New `tree.ts` helpers `compareInstant` (instant order, shared with `compareCreated`) and `latestNode`; App now uses `latestNode`. Also covered the one untested S4 error path: a failed "New conversation" shows the alert, leaves nothing half-opened, re-enables the button, and a retry works.
- Tests: `tree.test.ts` `latestNode` block (empty list, whole-second case, µs + offsets, tie → listed-last child, `compareInstant`); `hardening.test.tsx` create-failure + retry, and resume-on-newest through the real App; the App resume test failed before the fix.
- Decisions: ties (same instant, e.g. Windows' ~15 ms clock from Q10) go to the node listed *last*, not the larger id: `/tree` is parents-first, so a reply sharing its parent's timestamp still wins and we never resume on a non-leaf. That's why `latestNode` uses `compareInstant` rather than `compareCreated` (which breaks ties by id like the server's sibling order).
- Checks: backend ruff + pytest (285 passed, 13 skipped), frontend lint + 176 tests + build.
- No new deps.
- Next: backlog complete. Still suggested: re-merge of a grown branch replacing the earlier note (Q6, product call).

## 2026-11-03 22:58 UTC — Q16 (post-backlog: summary cache bound)
- Status: green
- Done: **Q16** the D2 ancestor-summary cache is process-wide, and its size cap (LRU eviction) was the one untested part of `budget.py`. New test in `test_context_budget.py` with `CACHE_SIZE=2`: a hit refreshes recency, overflow evicts the least recently used entry (not the newest), size stays at the cap, and an evicted history is summarized again. Mutation-checked: evicting the newest, or dropping `move_to_end` on a hit, both fail it. No app code change.
- Checks: backend ruff + pytest (286 passed, 13 skipped), frontend lint + 176 tests + build.
- No new deps.
- Next: backlog complete. Remaining uncovered lines are defensive (`??` fallbacks, a concurrent-merge race re-raise). Still suggested: re-merge of a grown branch replacing the earlier note (Q6, product call for Brandon).

## 2026-11-04 UTC — Q17 (post-backlog bug pass: new conversation vanishing from the list)
- Status: green
- Done: **Q17** App was the weakest-covered frontend module (84.8% branches). Found a race: "New conversation" is enabled while the conversation list is still loading (or after it failed). If the list response was a snapshot from before the create, it overwrote the list and the new conversation disappeared from the sidebar while its chat stayed open (same after a failed list + "Try again" that returns a stale list). App now remembers conversations created in this session (`useRef`) and `withCreated` puts any the fetched list lacks in front of it, without duplicates.
- Tests (`hardening.test.tsx`): create during a held list load → list shows new + old, new one `aria-current`; late list that already has it → listed once; failed list → create → retry still lists it; empty `/tree` → opens at the conversation root; list resolving after unmount → no React warnings. The first and third failed before the fix.
- Decisions: merge on the client rather than refetching after create: no extra request, and correct even if the server read is stale. A ref, not state, since it only feeds the list merge and never renders on its own.
- Checks: backend ruff + pytest (286 passed, 13 skipped, no backend change), frontend lint + 181 tests + build.
- No new deps (coverage measured with a temporary, unsaved `@vitest/coverage-v8`).
- Next: backlog complete. Still suggested: re-merge of a grown branch replacing the earlier note (Q6, product call for Brandon).

## 2026-11-04 22:50 UTC — Q18 (post-backlog bug pass: reply/note that can't be saved)
- Status: green
- Done: **Q18** the assistant node's commit at the end of `stream_reply` was unguarded: if it raised, the SSE body just stopped after the tokens (no `done`, no `error`; the UI fell back to "stream ended before the assistant finished") and the session was left mid-transaction. Reproduced with the case Q13 flagged: a provider token holding an unpaired surrogate (JSON-escaped half emoji) can't be UTF-8 encoded by SQLite or Postgres drivers. Now (1) reply text and merge-note text go through `scrub_surrogates` before saving (lone halves → U+FFFD; halves that arrived in separate chunks are recombined into the real emoji), and (2) a failed commit rolls back, is logged, and sends `error` "Could not save the reply; please retry" (DB internals stay in the log). The user turn stays childless, so a retry reuses it (no duplicate sibling).
- Tests: new `test_reply_save_errors.py` (6): lone surrogate in a reply saved as U+FFFD; split pair recombined; whitespace + half emoji isn't treated as blank; a forced flush failure (session `before_flush` hook) gives `error` and keeps the user turn; retry after it reuses the user turn; lone surrogate in a merge summary → 201 with U+FFFD. All 6 failed before the fix.
- Decisions: scrub the joined text, not each token, so split pairs survive; tokens are streamed unchanged. Generic error detail on save failure instead of the exception text, unlike provider errors, because a DB error message can leak schema/connection details.
- Checks: backend ruff + pytest (292 passed, 13 skipped), frontend lint + 181 tests + build (no frontend change).
- No new deps.
- Next: backlog complete. Still suggested: re-merge of a grown branch replacing the earlier note (Q6, product call for Brandon).

## 2026-11-05 UTC — Q19 (post-backlog bug pass: cut-off provider streams)
- Status: green
- Done: **Q19** if a provider's HTTP body just *ended* mid-reply (proxy idle timeout, Ollama restart, dropped Anthropic stream with a clean close), httpx raises nothing, so the tokens so far were saved as the complete assistant reply. Now each provider requires its own end marker: Ollama/OpenAI-style needs `[DONE]` or a `finish_reason` (new `StreamState.saw_done` from `iter_sse_json`), Anthropic needs `message_stop`. Otherwise `ProviderError` → the existing path: SSE `error`, nothing persisted, user turn reused on retry. Same protection applies to merge summaries and D2 ancestor summaries (they share the providers).
- Tests: new `test_stream_truncation.py` (10): cut-off Ollama stream raises after streaming the partial tokens; empty 200 body raises; `finish_reason` without `[DONE]` is complete; `length` finish is complete; Anthropic without `message_stop` (two cut points) raises; endpoint: cut-off → `error`, no assistant row, retry reuses the user turn; `iter_sse_json` records `[DONE]`, and skips blank data / non-object payloads / comment + `event:` lines with CRLF (covers two previously unhit branches). 5 failed before the fix.
- Decisions: accept `finish_reason` *or* `[DONE]` for OpenAI-style so other compatible servers that omit `[DONE]` still work; `length` (max_tokens) is a normal end, not a cut-off. Tokens still stream live; only the save is refused, matching how provider errors already worked.
- Checks: backend ruff + pytest (302 passed, 13 skipped), frontend lint + 181 tests + build (no frontend change).
- No new deps (coverage via a temporary, unsaved `pytest-cov`).
- Local agent: real Ollama sends `finish_reason` + `[DONE]`, so local runs should be unaffected; a failure mentioning "ended before the reply finished" would mean otherwise.
- Next: backlog complete. Still suggested: re-merge of a grown branch replacing the earlier note (Q6, product call for Brandon).

## 2026-11-05 22:55 UTC — Q20 (post-backlog bug pass: rate limiter under a client flood + repeated X-Forwarded-For)
- Status: green
- Done: **Q20** two D3 bugs. (1) `_prune` only dropped *full* buckets, so once 10k clients were active within one refill window (trivial with IPv6 addresses) the bucket dict grew without bound and every new client triggered a full O(n) rescan. Now, after dropping full buckets, it evicts the *fullest* remaining ones down to a 90% low-water mark: memory is capped and the scan runs at most once per ~1,000 new clients. (2) With `RATE_LIMIT_TRUST_PROXY=true`, `client_key` used the last hop of the *first* `X-Forwarded-For` line; a client-sent line plus one a proxy appended would let the client pick its own key. Now it takes the last hop across all lines (RFC 9110 list order).
- Tests (`test_rate_limit.py`, +3): flood of 500 new clients never exceeds a cap of 10 and the heavy (emptiest) user is never forgotten, so it stays limited; prune shrinks to the low-water mark; repeated XFF lines → last line's hop, and a blank last line doesn't hide earlier hops. All 3 failed before the fix; flipping eviction to "emptiest first" fails the flood test.
- Decisions: evict fullest-first because forgetting a bucket only refunds that client its missing tokens, so the cheapest eviction is the client closest to full; the actively limited client keeps its debt. Added `max_clients` to `TokenBucketLimiter` (default unchanged) so tests don't need 10k entries. Our nginx sends one combined XFF line, so Docker setups were not exposed to (2); it matters for other proxies.
- Checks: backend ruff + pytest (305 passed, 13 skipped), frontend lint + 181 tests + build (no frontend change).
- No new deps (coverage via a temporary, unsaved `pytest-cov`).
- Next: backlog complete. Still suggested: re-merge of a grown branch replacing the earlier note (Q6, product call for Brandon).

## 2026-11-06 UTC — Q21 (post-backlog bug pass: reply stream that drops or arrives garbled)
- Status: green
- Done: **Q21** `streamReply` trusted the body completely. (1) If the connection died mid-reply (Wi-Fi drop, proxy timeout), the body read rejected with the browser's bare `TypeError` ("network error" in Chrome, "Error in body stream" in Firefox) and that text was shown as the send error. Now a failed read becomes `ApiError(NETWORK_ERROR, "The connection was lost while the reply was streaming. Please retry.")`; aborts still pass through untouched. (2) Unparsable `data` threw a raw `SyntaxError`; payloads of the wrong shape were used as-is (a `token` without `text` appended the string "undefined"; a `done` without `id` became the next leaf). Now each event's payload is checked (`user`/`done` need id/role/content, `token` needs string `text`) and garbage raises a clear "malformed reply stream" `ApiError`. (3) A stream cut off inside its last event (parseSSE emits an unterminated final block) gave a JSON error; it now reads as "Reply stream ended before the assistant finished", same as any early end.
- Tests (`stream.test.ts`, +5): mid-stream read failure → `ApiError` with status 0 after the partial tokens were delivered; abort during the stream still an `AbortError`; malformed data mid-stream; `done` cut off mid-JSON; wrong-shaped `token`/`done`/`error` payloads. 4 failed before the fix (the abort one pins existing behaviour).
- Decisions: only the body *read* is wrapped (a small `readEvents` generator), so an exception thrown by a UI handler is never relabelled as a connection problem. A parse failure is held for one event: if the stream then ends it was a truncation, if more events follow it's garbage. Kept parseSSE's "emit an unterminated final block" behaviour (pinned by `sse.test.ts`) and handled truncation in the client instead.
- Checks: backend ruff + pytest (305 passed, 13 skipped, no backend change), frontend lint + 186 tests + build.
- No new deps.
## 2026-11-06 19:17 UTC — Q22 (post-backlog: local test runner preflight)
- Status: green
- Done: **Q22** `scripts/local_test.py` only checked that Ollama answered. If the model wasn't pulled, every `ollama` test would fail with a 404 and get logged as N failures, which the cloud sprint (step 2b) would then try to "fix" in app code. Now a preflight reads `/models`: not reachable or model missing → a single "not run" entry (e.g. "model `llama3.1:8b` not pulled (run `ollama pull llama3.1:8b`)") and pytest isn't started. Also: failure output containing ``` (e.g. a model reply with a code block in an assertion message) closed the report's code fence early; the fence is now longer than any backtick run in the body.
- Tests (`test_local_test_script.py`, +15): `/models` parsing (ids, unreachable, bad JSON, non-object, no `data`), `model_available` (`llama3.1` == `llama3.1:latest`, different tags don't match, registry host:port names), preflight reasons, `main --no-push` with a missing model writes "not run" and never calls pytest, backtick-safe fence, plain fence unchanged. All 15 failed before the change (`urlopen` monkeypatched; no network).
- Decisions: untagged names compare as `:latest` (Ollama's own rule); the tag check looks only after the last `/` so `host:5000/model` isn't mistaken for tagged. A missing model exits 0 like "Ollama down": it's a setup issue on the PC, not a test failure.
- Checks: backend ruff + pytest (321 passed, 13 skipped), frontend lint + 186 tests + build (no frontend change; rerun after rebasing on the concurrent Q21).
- No new deps (coverage via a temporary, unsaved `pytest-cov`).
- Local agent: nothing has been run on the PC yet (LOCAL_TESTS.md only has the setup entry); `python scripts/local_test.py` is ready.
- Next: backlog complete. Still suggested: re-merge of a grown branch replacing the earlier note (Q6, product call for Brandon).

## 2026-11-06 22:50 UTC — Q23 (post-backlog bug pass: merge that yanks you back after you moved on)
- Status: green
- Done: **Q23** BranchNav had the weakest frontend branch coverage (82.6%). Found a bug: click "Merge back", then click another node (or a crumb) while the summary is still being generated; when the merge finished, `onSelect(backTarget)` still fired from the unmounted BranchNav and pulled the user back to the old parent branch. BranchNav now tracks whether it's still mounted (App keys it per leaf) and only navigates / sets merge state if so. The merge request itself isn't aborted, so the note is still saved and shows up when the user visits that thread.
- Tests: `BranchNav.test.tsx` (+3): merge resolving after unmount doesn't call `onSelect`; failing after unmount stays quiet; a remount for a new leaf starts with no merge in progress. `App.test.tsx` (+1): full flow — side branch → Merge back (held) → click another branch → merge finishes → still on the other branch, tree selection unchanged. The two "doesn't navigate" tests failed before the fix.
- Decisions: don't abort the merge on navigation (user asked for it; the server work is the point), only drop the follow-up navigation. An error from a merge the user left behind is dropped rather than shown in another branch's UI.
- Checks: backend ruff + pytest (321 passed, 13 skipped, no backend change), frontend lint + 190 tests + build.
- No new deps (coverage via a temporary, unsaved `@vitest/coverage-v8`).
- Next: backlog complete. Still suggested: re-merge of a grown branch replacing the earlier note (Q6, product call for Brandon).

## 2026-11-07 18:55 UTC — Q24 (post-backlog bug pass: chat stuck loading after a quick back-and-forth)
- Status: green
- Done: **Q24** ChatView skips the path fetch when `leafId` equals `knownLeaf` (the leaf it last loaded or created), but starting a fetch for another leaf cleared the shown path without clearing `knownLeaf`. So A (loaded) → click B → click A again before B's path arrived hit the skip: B's fetch was cancelled, nothing refetched A, and the chat sat on "Loading messages…" forever. Same if B's load failed: going back to A left B's error up, and "Try again" hit the skip too. Also after a detour from a leaf created by a send. Fix: `knownLeaf` is reset to null whenever the effect clears the view, so it always means "the path currently shown".
- Tests (`ChatView.test.tsx`, +4): back to A while B's fetch is still pending → A reloads (3rd fetch is A's path); back to A after B 404s → A shown, no stale alert; detour from a send-created leaf and back → reloads it; B still loads normally. The first three failed before the fix.
- Decisions: refetch on return rather than caching the old path: the tree may have changed meanwhile (background replies, merges), and the extra GET is cheap.
- Checks: backend ruff + pytest (321 passed, 13 skipped, no backend change), frontend lint + 194 tests + build.
- No new deps (coverage via a temporary, unsaved `@vitest/coverage-v8`).
- Next: backlog complete. Still suggested: re-merge of a grown branch replacing the earlier note (Q6, product call for Brandon).

## 2026-11-07 19:05 UTC — Q25 (post-backlog bug pass: tree error from the previous conversation)
- Status: green
- Done: **Q25** TreeSidebar kept one untagged `error` that was only cleared by a successful fetch or "Try again". Switching conversation after a failed tree load (or a failed refresh) showed the *old* conversation's error, with its retry button, in place of "Loading tree…" for the new one until its fetch landed (forever if it hung). The error is now stored with its conversation id and only shown for that conversation, like the tree itself.
- Tests (`TreeSidebar.test.tsx`, +3): failed load in c1 → switch to c2 (pending) → loading, no alert; failed refresh in c1 → switch to c2 → no alert while loading or after it loads; a conversation's own error stays until "Try again" succeeds. The first two failed before the fix.
- Decisions: tag rather than clear on switch, so the same rule as the tree (`current`) applies and there's no extra effect.
- Checks: backend ruff + pytest (321 passed, 13 skipped, no backend change), frontend lint + 197 tests + build.
- No new deps.
- Next: backlog complete. Still suggested: re-merge of a grown branch replacing the earlier note (Q6, product call for Brandon).

## 2026-11-07 22:56 UTC — Q26 (post-backlog bug pass: NUL characters crash Postgres saves and the tree)
- Status: green
- Done: **Q26** nothing stripped U+0000. JSON allows `"\u0000"` (pasted from a binary file/terminal) and a model can emit one, but Postgres `text` rejects NUL (`psycopg.DataError`): a reply/quote/title/system prompt with one was a **500**, and a model reply with one streamed fine and then failed to save ("Could not save the reply"; merge summaries likewise). On SQLite it was stored, and a stored `\x000\x00` collided with `markdown_to_text`'s inline-code placeholders → `IndexError` → `GET /tree` 500. New `clean_text` (drop NUL + replace lone surrogates) used by the request validators (so `"\u0000"` alone counts as blank → 422; a NUL-only quote → no quote) and on provider output in `chat.stream_reply` and `merge.merge_branch`; `markdown_to_text`/`make_preview` drop NUL first so pre-existing SQLite rows can't crash `/tree`.
- Tests: new `test_nul_chars.py` (+30 with Postgres, +20 SQLite-only): request fields (content, quote, title, system_prompt) saved without NUL; NUL-only → 422 / no quote; model reply and merge summary with NULs saved clean and previewed; legacy NUL rows in `markdown_to_text` and `/tree`; `clean_text` cases. 28 of 30 failed before the fix, including the Postgres DataError and the IndexError.
- Decisions: remove NUL silently rather than 422 it: it's invisible, a user can't see what to fix, and it carries no meaning in chat text. Kept `scrub_surrogates` as is (it also scrubs the 422 echo, where NUL is fine in JSON). Endpoint tests use an engine fixture parametrized like `any_db`, so they also run on Postgres when `TEST_POSTGRES_URL` is set.
- Checks: backend ruff + pytest (341 passed, 23 skipped on SQLite; 359 passed, 5 skipped against a local Postgres 16 with `TEST_POSTGRES_URL`), frontend lint + 197 tests + build (no frontend change).
- No new deps (coverage via temporary, unsaved `pytest-cov` / `@vitest/coverage-v8`; backend 99%, frontend weakest branches are defensive fallbacks).
- Next: backlog complete. Still suggested: re-merge of a grown branch replacing the earlier note (Q6, product call for Brandon).

## 2026-11-08 19:51 UTC — Q27 (post-backlog bug pass: merging a long side branch overflows the summarizer)
- Status: green
- Done: **Q27** `merge.build_summary_prompt` clipped each turn to 4k chars but put *every* turn of the side branch into the summarizer prompt. A long side discussion (say 70 turns) produced a ~280k-char prompt, past the 60k-token context budget the reply path respects: on Anthropic a context-length 400 → merge 502, on Ollama silent front-truncation that drops the side question. Now the transcript is capped (`transcript_chars_for(CONTEXT_TOKEN_BUDGET, MERGE_MAX_TOKENS)`, floor 4k chars): the first turn (what was asked) and the last (what was concluded) are always kept, then as many of the most recent turns as fit, with "[… N earlier turns of the side discussion omitted …]" in between. The route passes `CONTEXT_TOKEN_BUDGET` through. README merge paragraph updated.
- Tests: new `test_merge_long_branch.py` (+6): 40-turn branch with a 3k-token budget stays under the cap and keeps the side question, the latest turn and the omitted marker; short branch unchanged; 70×4k-char branch bounded under the default budget; `_fit_turns` keeps a contiguous recent tail and the right count; first/last never dropped; cap floor. The endpoint tests failed before the fix.
- Decisions: reuse D2's budget and ~4 chars/token estimate rather than a new env var. Middle turns are dropped rather than summarized recursively: the note only needs the question and the conclusion, and one LLM call keeps merge latency predictable.
- Checks: backend ruff + pytest (347 passed, 23 skipped), frontend lint + 197 tests + build (no frontend change).
- No new deps.
- Next: backlog complete. Still suggested: re-merge of a grown branch replacing the earlier note (Q6, product call for Brandon).

## 2026-11-08 22:52 UTC — Q28 (post-backlog bug pass: unreadable 2xx bodies showed a raw SyntaxError)
- Status: green
- Done: **Q28** the JSON client did `await res.json()` on every 2xx. If something other than the API answered `/api/...` with a 200 (an SPA host's `index.html` fallback when the frontend is deployed without the API behind `/api`, a captive portal page), the user saw the browser's raw "Unexpected token '<', "<!doctype "... is not valid JSON" as the error; an empty/truncated body did the same, and a connection dropped mid-body showed a bare "network error". New `readJson` reads the body first (failure → `ApiError(NETWORK_ERROR, "The connection was lost while loading…")`), then parses it (failure → `ApiError(status, "The server sent a response the app couldn't read. Check that the API is running and reachable at /api.")`).
- Tests: `client.test.ts` (+4): 200 HTML page, empty and truncated 201 bodies, connection lost mid-body, JSON without a content type still parses. `hardening.test.tsx` (+1): App shows the readable message in the conversation-list alert, not "Unexpected token". 4 failed before the fix (the content-type one pins existing behaviour).
- Decisions: parse by content, not by `Content-Type`, so a backend/proxy that omits the header keeps working. Left `streamReply` alone: an HTML 200 there already ends as an `ApiError` ("Reply stream ended…") rather than a raw exception.
- Checks: backend ruff + pytest (347 passed, 23 skipped, no backend change), frontend lint + 202 tests + build.
- No new deps (coverage via a temporary, unsaved `@vitest/coverage-v8`).
- Local agent: still no real-model run logged in LOCAL_TESTS.md; `python scripts/local_test.py` on the PC is ready whenever.
- Next: backlog complete. Still suggested: re-merge of a grown branch replacing the earlier note (Q6, product call for Brandon).

## 2026-11-09 19:05 UTC — Q29 (post-backlog bug pass: DB connections held while the model generates)
- Status: green
- Done: **Q29** `chat.stream_reply` opened its session before building the prompt and kept it until the reply was saved, so the connection used for the path query stayed checked out (on Postgres, "idle in transaction") for the entire generation, including the D2 ancestor-summary call. With SQLAlchemy's default pool (5 + 10 overflow), ~15 concurrent replies starved every other request, which then failed after the 30 s pool timeout. Worse, a retry that reused a failed user turn (no commit) left the *request* session's read transaction open for the whole stream, so on a 1-connection pool the reply deadlocked against itself. Merge back held the request session's read transaction during the summarizer call too. Now: the path loads in a short session that closes before any LLM call, the reply is saved in a second short session, the reply route closes its request session before streaming, and `merge_branch` ends its read transaction before summarizing. A DB failure while loading the path now ends the stream with an `error` event instead of stopping after `user`.
- Tests: new `test_db_connection_during_llm.py` (+5) on a 1-connection pool with a provider that checks, mid-generation, that the pool is free and another session can query: normal replies, orphan-reuse retry (deadlocked before), D2 summary calls, merge, and the path-load error event. All 5 failed before the fix.
- Decisions: two short sessions rather than one long one; the prompt is plain data once built, so nothing needs a live session during streaming. `merge_branch` uses a no-op `commit()` (not `rollback()`) to end the transaction so the loaded rows aren't expired.
- Checks: backend ruff + pytest (352 passed, 23 skipped), frontend lint + 202 tests + build (no frontend change).
- No new deps.
- Next: backlog complete. Still suggested: re-merge of a grown branch replacing the earlier note (Q6, product call for Brandon).

## 2026-11-09 22:48 UTC — Q30 (post-backlog bug pass: "Ask about this" missing after a triple-click)
- Status: green
- Done: **Q30** `selectionInMessage` required both selection ends inside one message's `.content`. Real browsers often end a selection *just past* the visible highlight: a triple-click on a paragraph (Chrome ends it at offset 0 of the next block), a drag released below the last line (ends in the composer), or a backwards drag that starts at the very end of the previous message. In all of those the highlight is entirely inside one assistant reply, but "Ask about this" never appeared. Now the range's start/end each nominate a candidate message, and a candidate is accepted when the part of the range outside it is whitespace only. Element-level boundaries (`(li, 0)`) resolve to the child they point at.
- Tests: `selection.test.ts` (+8): triple-click ending at the next message, offset 0 of the next message's text, overshoot into the composer, backwards drag from the previous message's end, whole bubble selected at element level; still rejected: one real character of the next message, a whole-bubble selection that includes the quote block, wrong role. `ChatView.test.tsx` (+1): triple-click shape shows the button in that bubble. The 6 "accepts" tests failed before the fix.
- Decisions: whitespace-only spill is the rule (not "ignore the end boundary"), so a drag that really picks up a neighbour's text is still rejected and the quote always refers to one message. The quote text is still `selection.toString().trim()`, so the spill (a newline) never reaches the stored quote.
- Checks: backend ruff + pytest (352 passed, 23 skipped, no backend change), frontend lint + 211 tests + build.
- No new deps (coverage via a temporary, unsaved `@vitest/coverage-v8`; backend 99%, frontend 97% statements).
- Local agent: still no real-model run in LOCAL_TESTS.md; `python scripts/local_test.py` on the PC is ready.
- Next: backlog complete. Still suggested: re-merge of a grown branch replacing the earlier note (Q6, product call for Brandon).

## 2026-11-10 19:05 UTC — Q31 (post-backlog bug pass: nested merge notes lost when merging the parent branch)
- Status: green
- Done: **Q31** `merge.build_summary_prompt` sent only the side branch's own turns to the summarizer. If a nested side branch had already been merged *into* that branch (its note pinned on a branch message, shown inline and in the branch's later context), merging the branch back ignored it, so the nested conclusion never reached the main thread. `merge_branch` now loads the notes on the branch's messages and the transcript puts each one right after the message it is pinned to ("Note (merged back from a nested side discussion at this point): …", clipped like turns). Same skip rule as `chat.build_prompt` (a note whose source leaf is on this branch is left out); notes on the fork origin (sibling branches' conclusions) stay out. README merge paragraph updated.
- Tests: new `test_merge_nested_notes.py` (+5): merged nested note appears after its message and the nested turns aren't replayed; no notes → no marker; origin notes excluded; own-leaf note skipped; Q27 trimming keeps or drops a turn and its notes together, with the omitted count still counting turns. 3 failed before the fix (the other 2 pin existing behaviour).
- Decisions: the note rides in the same transcript entry as its message rather than as its own entry, so `_fit_turns` never orphans a note from the turn it belongs to. Notes are included as text, not as nested transcripts: the note already is the summary.
- Checks: backend ruff + pytest (357 passed, 23 skipped), frontend lint + 211 tests + build (no frontend change).
- No new deps (coverage via a temporary, unsaved `pytest-cov`; backend 99%).
- Next: backlog complete. Still suggested: re-merge of a grown branch replacing the earlier note (Q6, product call for Brandon).

## 2026-11-10 19:15 UTC — Q32 (post-backlog bug pass: "Merge back" from an unanswered question)
- Status: green
- Done: **Q32** BranchNav offered "Merge back" for any side-branch leaf, including a question selected as the leaf (clicked in the tree, or one whose reply failed). That merge summarizes the path *down to the question*, so the note presented an open question as the branch's conclusion (and, if the question had an answer, dropped it). The button is now disabled while the leaf is a user turn, with a hint ("Select an answer to merge back…") linked via `aria-describedby`; back/breadcrumb navigation is unchanged.
- Tests: `BranchNav.test.tsx` (+3): disabled with the hint and no request on a nested question; disabled on the side branch's first question; enabled with no hint on an answer. The 2 "disabled" tests failed before the fix.
- Decisions: frontend guard only, no new backend 409: the API stays permissive (a client may want to merge exactly what's on the path) and no existing behaviour changes for other callers. Reused the `composer-hint` class so no stylesheet change.
- Checks: backend ruff + pytest (357 passed, 23 skipped, no backend change), frontend lint + 214 tests + build.
- No new deps.
- Next: backlog complete. Still suggested: re-merge of a grown branch replacing the earlier note (Q6, product call for Brandon).

## 2026-11-10 22:58 UTC — Q33 (post-backlog bug pass: every conversation listed as "New conversation")
- Status: green
- Done: **Q33** The UI never sends a title, so every conversation was stored and listed as "New conversation" forever: past the first one, the sidebar list was a column of identical buttons and the only way to find a chat was to open each. Now `create_user_node` names a conversation after its first question when that question's parent is the root and the title is still the default (one-line preview, ≤60 chars, via `make_preview`). `repository.title_from_first_turn` is a single conditional `UPDATE … WHERE title = 'New conversation'`, committed with the user turn, so a title the user chose or an earlier first question set is never overwritten and two racing first questions can't both win. Frontend: when a reply lands in a conversation the list still shows as untitled, `App` silently refetches the list and takes the new titles (a failed refresh keeps the old label, never an error state). `DEFAULT_TITLE` is now one constant shared by the model and schema. SPEC create line updated.
- Tests: new `test_auto_title.py` (+7): first question names it; title is one line and capped with "…"; chosen title kept; follow-ups and a second top-level question don't rename; a deeper turn in an untitled conversation doesn't name it; the title is set even when the model fails (the question is saved); the repository update never overwrites. `App.test.tsx` (+3): new title replaces "New conversation" in the list; failed refresh keeps the list; no list refetch once titled. All failed before the fix (backend: import error / default title).
- Decisions: title from the question, not an LLM-generated summary: free, instant, deterministic, and no extra model call per conversation. Set at user-turn creation (not on `done`) so it doesn't depend on the model succeeding. Frontend refetches the list rather than predicting the title locally, so the server stays the only place the rule lives.
- Checks: backend ruff + pytest (364 passed, 23 skipped), frontend lint + 217 tests + build.
- No new deps.
- Next: backlog complete. Still suggested: re-merge of a grown branch replacing the earlier note (Q6, product call for Brandon); renaming a conversation from the UI if wanted (product call).

## 2026-11-11 19:10 UTC — Q34 (post-backlog bug pass: `%00` in a URL id was a 500 on Postgres)
- Status: green
- Done: **Q34** Every id-taking route (`GET /messages/{id}/path`, `GET /conversations/{id}/tree`, `POST /messages/{id}/reply`, `POST /branches/{id}/merge`) passed the raw path segment to the database. `%00` decodes to NUL, which Postgres refuses in any query parameter (psycopg DataError), so all four returned 500 on Postgres (SQLite just found nothing and 404'd). Q26 cleaned NUL out of request *bodies* but not URLs. `repository.get_message`, `get_path` and `get_tree` (the only entry points for URL ids) now 404 an id containing NUL before querying; the detail shows it as `\x00`, never a raw NUL.
- Tests: `test_nul_chars.py` (+7 cases, SQLite and Postgres): each route with a real id + `%00` is 404 with a NUL-free detail, adds no rows, and the app serves the same conversation normally afterwards; ids of only/inner NUL 404. Verified against a local Postgres 16 (`TEST_POSTGRES_URL`): 11 failed before the fix (7 Postgres 500s, 4 raw-NUL details).
- Decisions: 404, not 422: generated ids are hex and can never contain NUL, so "not found" is the truthful answer on both databases, and the guard lives in the repository so every caller (chat, merge) gets it. No id-format regex on routes: that would turn today's 404s for unknown ids into 422s and change the API contract.
- Checks: backend ruff + pytest (371 passed, 30 skipped; with Postgres 396 passed, 5 skipped), frontend lint + 217 tests + build (no frontend change).
- No new deps.
- Next: backlog complete. Still suggested: re-merge of a grown branch replacing the earlier note (Q6, product call for Brandon); renaming a conversation from the UI if wanted (product call).

## 2026-11-11 22:55 UTC — Q35 (post-backlog bug pass: a failed first reply left the sidebar stale)
- Status: green
- Done: **Q35** The server saves a question (and, since Q33, titles the conversation from a first question) *before* the model runs, so when the reply fails the question is a real, childless tree node. But the UI only refreshed the tree and the conversation list on a *successful* reply, so after a failed reply the sidebar tree didn't show the question and the list still said "New conversation" until a later reply or a reload. `ChatView` now has `onQuestionPersisted`, called with the user node when the `user` event arrived but the reply failed (attached or detached sends); `App` routes it to the same handler as replies (`onTurnPersisted`: bump the tree refresh key, refresh titles if still untitled). Moved a misplaced doc comment in `App` onto `replyCounts`.
- Tests: `ChatView.test.tsx` (+4): reported on a model failure after `user`; not reported for a 429 (no `user` event) or for a successful reply; reported for a detached send without surfacing its error. `App.test.tsx` (+1): first question fails → list shows the server's title and the tree shows the question, draft kept. 3 failed before the fix.
- Decisions: a separate callback rather than widening `onReplyPersisted`, so "a reply landed" keeps its meaning for callers; App treats both as "the tree changed". Only fires once the server confirmed the question, so a request rejected before saving (409/422/429) never triggers a refetch.
- Checks: backend ruff + pytest (371 passed, 30 skipped, no backend change), frontend lint + 222 tests + build.
- No new deps.
- Next: backlog complete. Still suggested: re-merge of a grown branch replacing the earlier note (Q6, product call for Brandon); renaming a conversation from the UI if wanted (product call).

## 2026-11-12 18:55 UTC — Q36 (post-backlog bug pass: blank/invalid numeric env settings were a 500 on every reply)
- Status: green
- Done: **Q36** Every numeric setting (`LLM_MAX_TOKENS`, `MERGE_MAX_TOKENS`, `CONTEXT_TOKEN_BUDGET`, `CONTEXT_SUMMARY_MAX_TOKENS`, `RATE_LIMIT_PER_MINUTE`, `RATE_LIMIT_BURST`, `DB_WAIT_ATTEMPTS`) was a bare `int(os.getenv(...))` read per request. `LLM_MAX_TOKENS=` (blank, the way `.env.example` leaves `ANTHROPIC_API_KEY=`) or a typo like `60k` made every reply a 500 *after* the question was saved (and the conversation renamed from it), and every merge a 500, while `/healthz` stayed green. New `app/config.py` (`env_int`/`env_float`, `ConfigError`): unset or blank → default; non-numeric, below the minimum (0 tokens, burst 0), NaN/inf → `ConfigError` naming the variable and value. `create_app` reads all of them once (`check_settings`), so a bad value stops the API at startup with a clear message. The reply route now reads its settings before saving the question, so no orphan turn is ever left by a config error. `.env.example` notes the rule.
- Tests: new `test_env_settings.py` (+22): blank/whitespace → default for every setting; whitespace-padded numbers parse; `60k`/`1.5`/`1e3`/`0x10` and `0`/`-5` rejected with the name; floats reject nan/inf/negative; each setting with `60k` fails `create_app`; `0` budget/tokens/burst fail startup; a reply with blank settings streams to `done`; a setting broken after startup saves no question. Reproduced first: blank `LLM_MAX_TOKENS` → 500 with the question and title saved.
- Decisions: fail fast at startup rather than silently falling back on a malformed value: a typo'd budget silently becoming 60k would hide a cost guard being off. Blank is the one "invalid" form treated as default, since that's how .env files and compose `${VAR:-}` express "unset". Services still re-read env per request (tests vary it per test; no behaviour change).
- Checks: backend ruff + pytest (393 passed, 30 skipped), frontend lint + 222 tests + build (no frontend change, run at sprint start).
- No new deps.
- Next: backlog complete. Still suggested: re-merge of a grown branch replacing the earlier note (Q6, product call for Brandon); renaming a conversation from the UI if wanted (product call).

## 2026-11-12 22:46 UTC — Q37 (post-backlog bug pass: local test runner reported "tested nothing" as a clean run)
- Status: green
- Done: **Q37** `scripts/local_test.py` (still never run on the PC) had two ways to produce a misleading LOCAL_TESTS.md entry. (1) The script is stdlib-only, so it can be started with a Python lacking the backend deps (system Python instead of the venv); pytest then died before writing its JUnit file and the entry just said "pytest crashed (exit 1), no results file". Now `missing_deps_note` probes the same interpreter for `pytest`, `fastapi`, `sqlalchemy`, `httpx` first and logs "not run — backend deps missing for <python> (<modules>); run with the backend's venv…" (an unstartable interpreter is reported too). (2) Preflight checks Ollama, but the tests' own 1s probe can still fail (model loading, different base URL); every test then skipped and the entry read "0/N passed, Failures: none", a green-looking run that tested nothing. Now `all_skipped_note` turns that into "not run — all N `ollama` tests skipped: <first skip reason>" (commit message "local tests: not run on <sha>"). `parse_junit` keeps the first skip message.
- Tests: `test_local_test_script.py` (+8): skip reason parsed; all-skipped note (all/mixed/none collected); `run_pytest` with a faked pytest subprocess writing an all-skipped and a mixed JUnit file; missing deps → no pytest run; deps probe with this Python (none missing), a fake missing module, and a nonexistent interpreter. 8 failed before the fix.
- Decisions: "not run" rather than failures for both, matching CLAUDE.md's rule that an environment problem is logged as the result, so the cloud agent never treats it as app bugs to fix. The deps probe uses `importlib.util.find_spec` in a subprocess of the same interpreter that runs pytest (stdlib only, no imports of app code).
- Checks: backend ruff + pytest (401 passed, 30 skipped), frontend lint + 222 tests + build (no frontend change). Pre-existing ruff I001 in `scripts/screenshot.py` when linted from the repo root (outside the required `backend/` check), left as is.
- No new deps.
- Next: backlog complete. Still suggested: re-merge of a grown branch replacing the earlier note (Q6, product call for Brandon); renaming a conversation from the UI if wanted (product call).
