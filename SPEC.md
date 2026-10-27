# Branch Chat — Spec

## Problem
When an AI answer confuses you, you open a new tab, re-explain the context, and lose the main thread.
Branch Chat lets you branch a side question off any message, explore it with the full parent context, then jump back — all in one tab.

## Core concepts
- **Conversation**: a tree of messages.
- **Message node**: `id, conversation_id, parent_id (nullable), role (user|assistant|system), content, quote (nullable), created_at`.
- **Branch**: any child of a message that already has a child. Siblings never see each other's content.
- **Context for an LLM call** = the path from the root to the current node (recursive CTE), oldest first.
- **Quote branch**: user highlights text in an assistant reply → "Ask about this" → new user node whose `quote` field holds the highlighted text; the prompt includes it.
- **Merge back**: summarize a side branch into a short note attached to the branch's origin message (stored as a `note` row, shown inline in the main thread, and included in later context for descendants of that origin).

## Architecture
- **Backend**: Python 3.12, FastAPI, SQLAlchemy 2.x, Alembic, Pydantic v2.
  - DB: Postgres in docker-compose for real use; SQLite for tests. Path query must work on both (recursive CTE).
  - Streaming: Server-Sent Events (SSE) for assistant tokens.
- **LLM layer**: `LLMProvider` interface with `stream(messages, max_tokens) -> AsyncIterator[str]`.
  - `MockProvider` (default when `LLM_PROVIDER` unset; deterministic, used in all tests).
  - `OllamaProvider` (OpenAI-compatible endpoint, `OLLAMA_BASE_URL`, default `http://localhost:11434/v1`).
  - `AnthropicProvider` (model `claude-haiku-5-5`, `ANTHROPIC_API_KEY`, prompt caching on the shared ancestor prefix).
  - Selected by env var `LLM_PROVIDER=mock|ollama|anthropic`.
- **Cost guards**: `max_tokens` cap; context budget — if root→node path exceeds `CONTEXT_TOKEN_BUDGET` (default 60k est. tokens), summarize oldest ancestors into one system message; per-IP token-bucket rate limit middleware.
- **Frontend**: React + TypeScript + Vite. Vitest + Testing Library for tests.
  - Main pane: the active path rendered as a normal chat.
  - Sidebar: collapsible tree / minimap of the conversation; click a node to make it the active leaf.
  - Text selection on an assistant message shows "Ask about this".
  - Breadcrumb + "Back to main thread" button when on a side branch.
  - Merge-back button on side branches.

## API (v1)
- `POST /api/conversations` → create
- `GET /api/conversations` → list
- `GET /api/conversations/{id}/tree` → all nodes (id, parent_id, role, preview) for the sidebar
- `GET /api/messages/{id}/path` → root→node messages
- `POST /api/messages/{id}/reply` body `{content, quote?}` → creates user child of `{id}`, streams assistant child via SSE, persists it when done
- `POST /api/branches/{leaf_id}/merge` → summarizes branch from its fork point to `leaf_id`, stores note on the fork-origin message
- `GET /healthz`

## Testing tiers
- **Fast tests** (cloud, every sprint): `LLM_PROVIDER=mock`, SQLite, no network. Must always pass on `main`.
- **Real-model tests** (Brandon's PC): pytest marker `ollama`, auto-skipped when Ollama is unreachable. Run via `scripts/local_test.py`, results in `LOCAL_TESTS.md`. They check structure (streaming, persistence, context isolation, quotes, merge notes), never exact wording.

## Definition of done (MVP)
- `docker compose up` runs Postgres + API + web; works with `LLM_PROVIDER=ollama` locally.
- All backend and frontend tests pass in CI (GitHub Actions) with `LLM_PROVIDER=mock`.
- README has a GIF/screenshot placeholder, architecture diagram (Mermaid), setup steps, and design-decision notes.
