# Rules for autonomous sprints

You are running an unattended sprint on this repo. Nobody is watching. Follow this loop exactly.

## Sprint loop
1. `git pull`. Read `SPEC.md`, `BACKLOG.md`, and the last ~5 entries of `PROGRESS.md`.
2. If the last entry says the build is red, your only job this sprint is to make it green.
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

## Code style
- Python: type hints everywhere, ruff defaults, small modules (`app/api`, `app/db`, `app/llm`, `app/services`).
- TS: strict mode, function components, no `any`.
- Write a short docstring/comment on anything non-obvious (the CTE, SSE handling, caching) so the owner can explain it in interviews.
