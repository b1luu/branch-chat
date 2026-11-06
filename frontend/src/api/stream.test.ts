import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchCall, jsonResponse, mockFetch, sseEvent, sseResponse } from "../test/fetch.ts";
import { msg } from "../test/messages.ts";
import { ApiError, NETWORK_ERROR, streamReply } from "./client.ts";

afterEach(() => {
  vi.unstubAllGlobals();
});

const user = msg({ id: "u1", parent_id: "p1", content: "hi" });
const assistant = msg({ id: "a1", parent_id: "u1", role: "assistant", content: "Hello there" });

describe("streamReply", () => {
  it("POSTs the body and dispatches user → tokens → done", async () => {
    const fetchMock = mockFetch(
      sseResponse([
        sseEvent("user", user),
        sseEvent("token", { text: "Hello" }),
        sseEvent("token", { text: " there" }),
        sseEvent("done", assistant),
      ]),
    );
    const onUser = vi.fn();
    const tokens: string[] = [];
    const result = await streamReply("p1", { content: "hi", quote: "q" }, { onUser, onToken: (t) => tokens.push(t) });

    expect(result).toEqual(assistant);
    expect(onUser).toHaveBeenCalledWith(user);
    expect(tokens).toEqual(["Hello", " there"]);
    const [url, init] = fetchCall(fetchMock);
    expect(url).toBe("/api/messages/p1/reply");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string)).toEqual({ content: "hi", quote: "q" });
  });

  it("keeps newlines inside token text (JSON-encoded data)", async () => {
    mockFetch(sseResponse([sseEvent("token", { text: "a\n\nb" }), sseEvent("done", assistant)]));
    const tokens: string[] = [];
    await streamReply("p1", { content: "x" }, { onToken: (t) => tokens.push(t) });
    expect(tokens).toEqual(["a\n\nb"]);
  });

  it("rejects with the provider's detail on an error event", async () => {
    mockFetch(sseResponse([sseEvent("user", user), sseEvent("error", { detail: "LLM provider error: down" })]));
    await expect(streamReply("p1", { content: "x" })).rejects.toThrow("LLM provider error: down");
  });

  it("rejects with ApiError on an HTTP error before streaming", async () => {
    mockFetch(jsonResponse({ detail: "Message not found" }, 404));
    const err = await streamReply("nope", { content: "x" }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 404, message: "Message not found" });
  });

  it("rejects if the stream ends without done", async () => {
    mockFetch(sseResponse([sseEvent("user", user), sseEvent("token", { text: "par" })]));
    await expect(streamReply("p1", { content: "x" })).rejects.toThrow(/ended before/);
  });

  it("ignores unknown events", async () => {
    mockFetch(sseResponse([sseEvent("future", { x: 1 }), sseEvent("done", assistant)]));
    await expect(streamReply("p1", { content: "x" })).resolves.toEqual(assistant);
  });

  it("rejects with a readable ApiError when the connection drops mid-stream", async () => {
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(sseEvent("user", user) + sseEvent("token", { text: "par" })));
      },
      pull(controller) {
        // What the browser does when the socket dies: the body read rejects with a TypeError.
        controller.error(new TypeError("network error"));
      },
    });
    mockFetch(new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }));
    const tokens: string[] = [];
    const err = await streamReply("p1", { content: "x" }, { onToken: (t) => tokens.push(t) }).catch((e: unknown) => e);
    expect(tokens).toEqual(["par"]);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: NETWORK_ERROR });
    expect((err as Error).message).toMatch(/connection was lost/i);
  });

  it("rethrows an abort during the stream untouched", async () => {
    const controller = new AbortController();
    const body = new ReadableStream<Uint8Array>({
      pull(c) {
        controller.abort();
        c.error(new DOMException("The operation was aborted.", "AbortError"));
      },
    });
    mockFetch(new Response(body, { status: 200 }));
    const err = await streamReply("p1", { content: "x" }, {}, controller.signal).catch((e: unknown) => e);
    expect(err).not.toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ name: "AbortError" });
  });

  it("rejects with ApiError, not a SyntaxError, on malformed data mid-stream", async () => {
    mockFetch(
      sseResponse([sseEvent("user", user), "event: token\ndata: {not json\n\n", sseEvent("done", assistant)]),
    );
    const err = await streamReply("p1", { content: "x" }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as Error).message).toMatch(/malformed/i);
  });

  it("treats a stream cut off in the middle of the done event as ended early", async () => {
    const full = sseEvent("done", assistant);
    mockFetch(sseResponse([sseEvent("user", user), full.slice(0, full.length - 12)]));
    const err = await streamReply("p1", { content: "x" }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as Error).message).toMatch(/ended before/);
  });

  it("rejects on events whose payload has the wrong shape", async () => {
    mockFetch(sseResponse([sseEvent("token", { txt: "oops" }), sseEvent("done", assistant)]));
    await expect(streamReply("p1", { content: "x" }, { onToken: () => undefined })).rejects.toThrow(/malformed/i);
    mockFetch(sseResponse([sseEvent("done", { content: "no id" })]));
    await expect(streamReply("p1", { content: "x" })).rejects.toThrow(/malformed/i);
    mockFetch(sseResponse([sseEvent("error", "boom")]));
    await expect(streamReply("p1", { content: "x" })).rejects.toBeInstanceOf(ApiError);
  });
});
