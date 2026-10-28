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
