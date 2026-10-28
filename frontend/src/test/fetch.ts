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
