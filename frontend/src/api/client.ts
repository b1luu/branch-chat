/**
 * Thin typed wrapper over the backend's JSON endpoints.
 *
 * All paths are relative (`/api/...`): in dev, Vite proxies them to FastAPI; in
 * production the web container serves the app on the same origin as the API.
 * The streaming reply endpoint (SSE) gets its own helper in F2.
 */
import type { Conversation, ConversationCreate, Message, Tree } from "./types.ts";

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
