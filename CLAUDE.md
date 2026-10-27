# Rules for agents working on this repo

Two kinds of agents work here, and they coordinate only through files in this repo:
- **Cloud sprint agent** (scheduled, no GPU, mock LLM): writes all code. Logs to `PROGRESS.md`.
- **Local test agent** (Brandon's PC, real model via Ollama): runs real-model tests and reports. Logs to `LOCAL_TESTS.md`. **It never edits app code**, so the two agents never collide.

If you're on Brandon's PC or were asked to "run the local tests", follow **Local test agent** below. Otherwise you're the cloud sprint agent.

## Cloud sprint loop
1. `git pull`. Read `SPEC.md`, `BACKLOG.md`, the last ~5 entries of `PROGRESS.md`, and the last ~3 entries of `LOCAL_TESTS.md`.
2. If the last PROGRESS entry says the build is red, your only job this sprint is to make it green.
2b. Else if the latest `LOCAL_TESTS.md` entry has failures **and** no later PROGRESS entry says they were fixed, fix those first. Read the failure output carefully: if a test assumed exact model wording, make the test check structure instead (and say so in the commit); otherwise fix the app code. You can't run Ollama here, so reproduce the failure with the mock or a unit test before fixing. Log it as `LOCAL-FIX` in PROGRESS.md, referencing the LOCAL_TESTS commit SHA.
3. Otherwise pick the first unchecked backlog item (skip ones marked BLOCKED).
4. Implement it with tests. Keep the change focused on that item.
5. Run the full check:
   - backend: `cd backend && ruff check . && pytest -q`
   - frontend (once it exists): `cd frontend && npm ci && npm run lint && npm test -- --run && npm run build`
6. If anything fails: read the error, fix the cause, rerun. Up to 5 fix attempts.
7. Green → check the item off in `BACKLOG.md`, add a `PROGRESS.md` entry, commit, push to `main`.
8. Still red after 5 attempts → push the work to a branch `wip/<item-id>`, leave `main` green, add a PROGRESS.md entry with the exact error and what you tried, and mark the item `BLOCKED` only if it's a real blocker (missing secret, external service).
9. If time and context remain, take the next item. Stop after ~3 items or when the work gets large.

## Hard rules
- `main` must always be green. Never push failing tests to `main`.
- Never delete, skip, or weaken a test to make it pass. Fix the code. If a test is genuinely wrong, explain why in the commit message.
- Tests never call real networks or real LLMs. Use `LLM_PROVIDER=mock` and mocked HTTP.
- Never commit secrets, `.env` files, API keys, or tokens. Use `.env.example`.
- Never force-push or rewrite history.
- Don't add dependencies without a reason; note new ones in the PROGRESS entry.
- Small, descriptive commits: `B3: recursive CTE path query + tests`.

## PROGRESS.md entry format
```
## <YYYY-MM-DD HH:MM UTC> — <item ids>
- Status: green | red (on wip/<branch>)
- Done: <what changed, 1–3 bullets>
- Decisions: <any design choices worth knowing for interviews>
- Next: <next item id>
```

## Local test agent (Brandon's PC)
1. Make sure Ollama is running (`ollama ps` or `ollama list`) and the model is pulled (default `llama3.1:8b`; pull it if missing).
2. Run `python scripts/local_test.py` from the repo root. Before L1 exists, there's nothing to run: say so and stop.
3. Only `LOCAL_TESTS.md` gets committed. Never edit app code, tests, or other files from the PC; if something's broken, the report is how you hand it to the cloud agent.
4. If Ollama isn't reachable or the GPU isn't used (`ollama ps` shows CPU), log that as the result instead of failures.
5. Commit message: `local tests: <pass>/<total> on <sha>`.

## LOCAL_TESTS.md entry format
```
## <YYYY-MM-DD HH:MM UTC> — local run
- Commit: <sha tested>
- Model: <ollama model>
- Result: <passed>/<total> passed (<skipped> skipped)
- Failures:
  - <test name>: <first ~30 lines of the error>
```

## Code style
- Python: type hints everywhere, ruff defaults, small modules (`app/api`, `app/db`, `app/llm`, `app/services`).
- TS: strict mode, function components, no `any`.
- Write a short docstring/comment on anything non-obvious (the CTE, SSE handling, caching) so the owner can explain it in interviews.
