/**
 * Thin typed wrapper over the backend's JSON endpoints.
 *
 * All paths are relative (`/api/...`): in dev, Vite proxies them to FastAPI; in
 * production the web container serves the app on the same origin as the API.
 */
import { parseSSE, type SSEEvent } from "./sse.ts";
import type { Conversation, ConversationCreate, Message, Note, ReplyCreate, Tree } from "./types.ts";

export class ApiError extends Error {
  /** HTTP status, or `NETWORK_ERROR` (0) when the request never got a response. */
  readonly status: number;
  /** Seconds to wait before retrying, from a 429's `Retry-After` header. */
  readonly retryAfter: number | undefined;

  constructor(status: number, message: string, retryAfter?: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

/** `ApiError.status` when fetch itself failed (server down, offline, CORS). */
export const NETWORK_ERROR = 0;
export const NETWORK_ERROR_MESSAGE = "Can't reach the server. Check your connection and that the API is running.";

/** One entry of FastAPI's 422 body: `{"loc": ["body", "content"], "msg": "..."}`. */
interface ValidationIssue {
  loc?: unknown;
  msg?: unknown;
}

/**
 * Turn FastAPI's 422 `detail` list into one readable line, e.g.
 * `content: must not be blank`. Pydantic prefixes custom errors with
 * "Value error, " and every location with "body"; neither helps a user.
 */
export function formatValidationDetail(detail: readonly ValidationIssue[]): string {
  const parts = detail.map((issue) => {
    const msg = typeof issue.msg === "string" ? issue.msg.replace(/^Value error, /, "") : "is invalid";
    const loc = Array.isArray(issue.loc) ? issue.loc.filter((p) => p !== "body").join(".") : "";
    return loc ? `${loc}: ${msg}` : msg;
  });
  return parts.join("; ") || "Invalid request";
}

/** `Retry-After` as whole seconds (we only send the delta-seconds form). */
function retryAfterSeconds(res: Response): number | undefined {
  const raw = res.headers.get("Retry-After");
  const n = raw === null ? NaN : Number(raw);
  return Number.isFinite(n) && n >= 0 ? Math.ceil(n) : undefined;
}

/** Pull FastAPI's `{"detail": ...}` out of an error body, falling back to the status text. */
async function errorMessage(res: Response): Promise<string> {
  try {
    const body: unknown = await res.json();
    if (body && typeof body === "object" && "detail" in body) {
      const { detail } = body as { detail: unknown };
      if (typeof detail === "string") return detail;
      if (Array.isArray(detail)) return formatValidationDetail(detail as ValidationIssue[]);
      return JSON.stringify(detail);
    }
  } catch {
    // Non-JSON error body; use the status line instead.
  }
  return `${res.status} ${res.statusText}`.trim();
}

/** Build the `ApiError` for a non-2xx response; 429s get a "try again in N s" hint. */
async function httpError(res: Response): Promise<ApiError> {
  const message = await errorMessage(res);
  if (res.status !== 429) return new ApiError(res.status, message);
  const wait = retryAfterSeconds(res);
  const hint = wait === undefined ? "Please wait a moment and try again." : `Try again in ${wait} s.`;
  return new ApiError(429, `${message}. ${hint}`, wait);
}

/**
 * `fetch`, but a failure to get any response becomes an `ApiError` with a
 * readable message (the browser's is just "Failed to fetch"). Aborts are
 * rethrown untouched so callers can tell "cancelled" from "broken".
 */
async function send(path: string, init: RequestInit): Promise<Response> {
  try {
    return await fetch(path, init);
  } catch (err) {
    if (init.signal?.aborted || (err instanceof DOMException && err.name === "AbortError")) throw err;
    throw new ApiError(NETWORK_ERROR, NETWORK_ERROR_MESSAGE);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await send(path, {
    ...init,
    headers: { Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json" } : {}) },
  });
  if (!res.ok) throw await httpError(res);
  return (await res.json()) as T;
}

export function listConversations(): Promise<Conversation[]> {
  return request<Conversation[]>("/api/conversations");
}

export function createConversation(body: ConversationCreate = {}): Promise<Conversation> {
  return request<Conversation>("/api/conversations", { method: "POST", body: JSON.stringify(body) });
}

export function getTree(conversationId: string): Promise<Tree> {
  return request<Tree>(`/api/conversations/${encodeURIComponent(conversationId)}/tree`);
}

export function getPath(messageId: string): Promise<Message[]> {
  return request<Message[]>(`/api/messages/${encodeURIComponent(messageId)}/path`);
}

/**
 * Summarize the side branch ending at `leafId` into a note on its fork origin.
 * 409 if the leaf is on the main thread, 502 if the LLM fails.
 */
export function mergeBranch(leafId: string): Promise<Note> {
  return request<Note>(`/api/branches/${encodeURIComponent(leafId)}/merge`, { method: "POST" });
}

export interface ReplyHandlers {
  /** The persisted user node (sent first, before any tokens). */
  onUser?: (message: Message) => void;
  /** One chunk of assistant text. */
  onToken?: (text: string) => void;
}

/**
 * POST a reply under `parentId` and consume the SSE stream.
 *
 * Event protocol (see backend `chat.stream_reply`): `user` → `token`* → `done`,
 * or `error` instead of `done` if the provider fails. Every `data` is JSON.
 * Resolves with the persisted assistant message; rejects with `ApiError` for an
 * HTTP error status, a provider `error` event, or a stream that ends early.
 */
export async function streamReply(
  parentId: string,
  body: ReplyCreate,
  handlers: ReplyHandlers = {},
  signal?: AbortSignal,
): Promise<Message> {
  const res = await send(`/api/messages/${encodeURIComponent(parentId)}/reply`, {
    method: "POST",
    headers: { Accept: "text/event-stream", "Content-Type": "application/json" },
    body: JSON.stringify(body),
    ...(signal ? { signal } : {}),
  });
  if (!res.ok) throw await httpError(res);
  if (!res.body) throw new ApiError(res.status, "Reply stream had no body");

  // A parse failure is held for one step: if the stream then ends, the bad event
  // was the last, cut-off one (parseSSE emits an unterminated final block), so
  // it's reported as an early end; if more events follow, the data was garbage.
  let malformed = false;
  for await (const { event, data } of readEvents(res.body, signal)) {
    if (malformed) throw new ApiError(res.status, MALFORMED_STREAM_MESSAGE);
    let payload: unknown;
    try {
      payload = JSON.parse(data);
    } catch {
      malformed = true;
      continue;
    }
    switch (event) {
      case "user":
        if (!isMessage(payload)) throw new ApiError(res.status, MALFORMED_STREAM_MESSAGE);
        handlers.onUser?.(payload);
        break;
      case "token":
        if (!hasString(payload, "text")) throw new ApiError(res.status, MALFORMED_STREAM_MESSAGE);
        handlers.onToken?.(payload.text);
        break;
      case "done":
        if (!isMessage(payload)) throw new ApiError(res.status, MALFORMED_STREAM_MESSAGE);
        return payload;
      case "error":
        throw new ApiError(res.status, hasString(payload, "detail") ? payload.detail : "The reply failed");
      // Unknown events are ignored so the server can add new ones safely.
    }
  }
  throw new ApiError(res.status, "Reply stream ended before the assistant finished");
}

export const MALFORMED_STREAM_MESSAGE = "The server sent a malformed reply stream. Please retry.";
export const STREAM_LOST_MESSAGE = "The connection was lost while the reply was streaming. Please retry.";

function hasString<K extends string>(value: unknown, key: K): value is Record<K, string> {
  return typeof value === "object" && value !== null && typeof (value as Record<string, unknown>)[key] === "string";
}

/** Enough of a `Message` to append it to the path and use its id as the next leaf. */
function isMessage(value: unknown): value is Message {
  return hasString(value, "id") && hasString(value, "role") && hasString(value, "content");
}

/**
 * `parseSSE`, but a body read that fails mid-stream (socket reset, Wi-Fi drop,
 * proxy timeout) becomes an `ApiError` instead of the browser's bare
 * "network error" TypeError. Aborts pass through untouched, like `send`.
 * Only the read is wrapped, so errors thrown by the caller's handlers between
 * events are never mislabelled as connection problems.
 */
async function* readEvents(body: ReadableStream<Uint8Array>, signal?: AbortSignal): AsyncGenerator<SSEEvent> {
  const events = parseSSE(body);
  for (;;) {
    let next: IteratorResult<SSEEvent>;
    try {
      next = await events.next();
    } catch (err) {
      if (signal?.aborted || (err instanceof DOMException && err.name === "AbortError")) throw err;
      throw new ApiError(NETWORK_ERROR, STREAM_LOST_MESSAGE);
    }
    if (next.done) return;
    yield next.value;
  }
}
