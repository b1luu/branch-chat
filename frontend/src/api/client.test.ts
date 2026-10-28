import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchCall, jsonResponse, mockFetch } from "../test/fetch.ts";
import { ApiError, createConversation, getPath, getTree, listConversations, mergeBranch } from "./client.ts";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("api client", () => {
  it("lists conversations with GET", async () => {
    const fetchMock = mockFetch(jsonResponse([]));
    await expect(listConversations()).resolves.toEqual([]);
    const [url, init] = fetchCall(fetchMock);
    expect(url).toBe("/api/conversations");
    expect(init?.method).toBeUndefined();
  });

  it("creates a conversation with a JSON POST body", async () => {
    const conv = { id: "c1", title: "T", created_at: "2026-10-28T00:00:00Z", root_message_id: "r1" };
    const fetchMock = mockFetch(jsonResponse(conv, 201));
    await expect(createConversation({ title: "T" })).resolves.toEqual(conv);
    const [url, init] = fetchCall(fetchMock);
    expect(url).toBe("/api/conversations");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string)).toEqual({ title: "T" });
    expect(init?.headers).toMatchObject({ "Content-Type": "application/json" });
  });

  it("fetches the tree and path with encoded ids", async () => {
    const fetchMock = mockFetch(jsonResponse({ conversation_id: "a/b", nodes: [] }), jsonResponse([]));
    await getTree("a/b");
    await getPath("m 1");
    expect(fetchCall(fetchMock, 0)[0]).toBe("/api/conversations/a%2Fb/tree");
    expect(fetchCall(fetchMock, 1)[0]).toBe("/api/messages/m%201/path");
  });

  it("throws ApiError with FastAPI's detail on failure", async () => {
    mockFetch(jsonResponse({ detail: "Message not found" }, 404));
    const err = await getPath("nope").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 404, message: "Message not found" });
  });

  it("stringifies validation-error details", async () => {
    mockFetch(jsonResponse({ detail: [{ msg: "too short" }] }, 422));
    await expect(createConversation({ title: "" })).rejects.toThrow("too short");
  });

  it("falls back to the status line for non-JSON errors", async () => {
    mockFetch(new Response("oops", { status: 502, statusText: "Bad Gateway" }));
    await expect(listConversations()).rejects.toThrow("502 Bad Gateway");
  });

  it("merges a branch with a bodiless POST and returns the note", async () => {
    const note = { id: "n1", message_id: "a1", source_leaf_id: "b/2", content: "Sum.", created_at: "2026-10-28T00:00:00Z" };
    const fetchMock = mockFetch(jsonResponse(note, 201));
    await expect(mergeBranch("b/2")).resolves.toEqual(note);
    const [url, init] = fetchCall(fetchMock);
    expect(url).toBe("/api/branches/b%2F2/merge");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBeUndefined();
  });

  it("surfaces a merge conflict as ApiError", async () => {
    mockFetch(jsonResponse({ detail: "message a2 is on the main thread; nothing to merge" }, 409));
    await expect(mergeBranch("a2")).rejects.toMatchObject({ status: 409, message: expect.stringContaining("main thread") });
  });
});
