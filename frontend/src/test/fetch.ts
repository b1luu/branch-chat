import { vi } from "vitest";

/** Build a JSON Response like the backend would send. */
export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Replace global fetch with a mock; `restoreMocks` in vite.config resets it between tests. */
export function mockFetch(...responses: Response[]) {
  const fn = vi.fn<typeof fetch>();
  for (const r of responses) fn.mockResolvedValueOnce(r);
  vi.stubGlobal("fetch", fn);
  return fn;
}

/** The `[url, init]` of the i-th fetch call; throws (failing the test) if there wasn't one. */
export function fetchCall(fn: ReturnType<typeof mockFetch>, i = 0): Parameters<typeof fetch> {
  const call = fn.mock.calls[i];
  if (!call) throw new Error(`fetch was called ${fn.mock.calls.length} time(s), expected call #${i}`);
  return call;
}

/** Format one SSE event the way the backend does (`event:` + JSON `data:`). */
export function sseEvent(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

/** A Response whose body emits the given text chunks, all at once. */
export function sseResponse(chunks: string[], status = 200): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(encoder.encode(c));
      controller.close();
    },
  });
  return new Response(body, { status, headers: { "Content-Type": "text/event-stream" } });
}

/** A streaming Response the test feeds chunk by chunk, to observe progressive rendering. */
export function controlledSSE(): { response: Response; push: (chunk: string) => void; close: () => void } {
  const encoder = new TextEncoder();
  let ctrl: ReadableStreamDefaultController<Uint8Array> | undefined;
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      ctrl = controller;
    },
  });
  return {
    response: new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    push: (chunk) => ctrl?.enqueue(encoder.encode(chunk)),
    close: () => ctrl?.close(),
  };
}
