/**
 * Thin typed wrapper over the backend's JSON endpoints.
 *
 * All paths are relative (`/api/...`): in dev, Vite proxies them to FastAPI; in
 * production the web container serves the app on the same origin as the API.
 */
import { parseSSE } from "./sse.ts";
import type { Conversation, ConversationCreate, Message, ReplyCreate, Tree } from "./types.ts";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** Pull FastAPI's `{"detail": ...}` out of an error body, falling back to the status text. */
async function errorMessage(res: Response): Promise<string> {
  try {
    const body: unknown = await res.json();
    if (body && typeof body === "object" && "detail" in body) {
      const { detail } = body as { detail: unknown };
      return typeof detail === "string" ? detail : JSON.stringify(detail);
    }
  } catch {
    // Non-JSON error body; use the status line instead.
  }
  return `${res.status} ${res.statusText}`.trim();
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json" } : {}) },
  });
  if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
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
  const res = await fetch(`/api/messages/${encodeURIComponent(parentId)}/reply`, {
    method: "POST",
    headers: { Accept: "text/event-stream", "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
  if (!res.body) throw new ApiError(res.status, "Reply stream had no body");

  for await (const { event, data } of parseSSE(res.body)) {
    const payload: unknown = JSON.parse(data);
    switch (event) {
      case "user":
        handlers.onUser?.(payload as Message);
        break;
      case "token":
        handlers.onToken?.((payload as { text: string }).text);
        break;
      case "done":
        return payload as Message;
      case "error":
        throw new ApiError(res.status, (payload as { detail: string }).detail);
      // Unknown events are ignored so the server can add new ones safely.
    }
  }
  throw new ApiError(res.status, "Reply stream ended before the assistant finished");
}
