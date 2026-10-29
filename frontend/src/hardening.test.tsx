/**
 * S4 hardening: error states with retry, readable API errors, loading
 * indicators, and client-side input validation.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App.tsx";
import {
  ApiError,
  NETWORK_ERROR,
  NETWORK_ERROR_MESSAGE,
  createConversation,
  formatValidationDetail,
  getPath,
  streamReply,
} from "./api/client.ts";
import type { TreeNode } from "./api/types.ts";
import ChatView, { MAX_CONTENT_CHARS } from "./components/ChatView.tsx";
import ErrorNotice from "./components/ErrorNotice.tsx";
import TreeSidebar from "./components/TreeSidebar.tsx";
import { controlledSSE, jsonResponse, mockFetch, routeFetch } from "./test/fetch.ts";
import { msg } from "./test/messages.ts";

afterEach(() => {
  vi.unstubAllGlobals();
});

const root = msg({ id: "root", role: "system", content: "sys" });
const u1 = msg({ id: "u1", parent_id: "root", content: "What is a CTE?" });
const a1 = msg({ id: "a1", parent_id: "u1", role: "assistant", content: "A common table expression." });
const conv = { id: "c1", title: "First", created_at: "2026-10-28T00:00:00Z", root_message_id: "root" };
const treeNode = (m: typeof root): TreeNode => ({
  id: m.id,
  parent_id: m.parent_id,
  role: m.role,
  preview: m.content,
  created_at: m.created_at,
});

/** A promise plus its resolver, to hold a request open while asserting the loading state. */
function deferred<T>() {
  let resolve: (value: T) => void = () => {};
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

/** A JSON Response whose body only arrives once `until` resolves (headers come first, like a slow server). */
function heldJson(until: Promise<unknown>, body: unknown, status = 200): Response {
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      await until;
      controller.enqueue(new TextEncoder().encode(JSON.stringify(body)));
      controller.close();
    },
  });
  return new Response(stream, { status, headers: { "Content-Type": "application/json" } });
}

describe("api client errors", () => {
  it("turns a 422 detail list into one readable line", async () => {
    mockFetch(
      jsonResponse(
        {
          detail: [
            { loc: ["body", "content"], msg: "Value error, must not be blank", type: "value_error" },
            { loc: ["body", "quote"], msg: "String should have at most 20000 characters" },
          ],
        },
        422,
      ),
    );
    await expect(createConversation()).rejects.toMatchObject({
      status: 422,
      message: "content: must not be blank; quote: String should have at most 20000 characters",
    });
  });

  it("formats validation issues without a location or message", () => {
    expect(formatValidationDetail([{ msg: "bad" }, { loc: ["body"] }])).toBe("bad; is invalid");
    expect(formatValidationDetail([])).toBe("Invalid request");
  });

  it("adds a countdown hint and retryAfter to 429s", async () => {
    mockFetch(
      new Response(JSON.stringify({ detail: "Rate limit exceeded" }), {
        status: 429,
        headers: { "Content-Type": "application/json", "Retry-After": "7" },
      }),
    );
    const err = await getPath("a1").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 429, retryAfter: 7, message: "Rate limit exceeded. Try again in 7 s." });
  });

  it("handles a 429 without Retry-After", async () => {
    mockFetch(jsonResponse({ detail: "Rate limit exceeded" }, 429));
    await expect(getPath("a1")).rejects.toMatchObject({
      retryAfter: undefined,
      message: "Rate limit exceeded. Please wait a moment and try again.",
    });
  });

  it("rate-limits replies the same way", async () => {
    mockFetch(
      new Response(JSON.stringify({ detail: "Rate limit exceeded" }), { status: 429, headers: { "Retry-After": "3" } }),
    );
    await expect(streamReply("a1", { content: "hi" })).rejects.toMatchObject({ status: 429, retryAfter: 3 });
  });

  it("turns a network failure into a readable ApiError", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(getPath("a1")).rejects.toMatchObject({ status: NETWORK_ERROR, message: NETWORK_ERROR_MESSAGE });
    await expect(streamReply("a1", { content: "hi" })).rejects.toMatchObject({ status: NETWORK_ERROR });
  });

  it("rethrows aborts untouched so callers can ignore them", async () => {
    const controller = new AbortController();
    controller.abort();
    const abort = new DOMException("The operation was aborted.", "AbortError");
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockRejectedValue(abort));
    await expect(streamReply("a1", { content: "hi" }, {}, controller.signal)).rejects.toBe(abort);
  });
});

describe("ErrorNotice", () => {
  it("shows the title and message, and retries on click", async () => {
    const onRetry = vi.fn();
    render(<ErrorNotice title="Couldn't load" message="boom" onRetry={onRetry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Couldn't load: boom");
    await userEvent.setup().click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("has no button without onRetry, and disables it while retrying", () => {
    const { rerender } = render(<ErrorNotice title="Oops" message="x" />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    rerender(<ErrorNotice title="Oops" message="x" onRetry={vi.fn()} retrying />);
    expect(screen.getByRole("button", { name: "Retrying…" })).toBeDisabled();
  });
});

describe("App error and loading states", () => {
  it("retries loading the conversation list", async () => {
    const fetchMock = mockFetch(jsonResponse({ detail: "boom" }, 500), jsonResponse([conv]));
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load conversations: boom");
    await userEvent.setup().click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("list", { name: "Conversations" })).toHaveTextContent("First");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("shows a server-unreachable message instead of 'Failed to fetch'", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockRejectedValue(new TypeError("Failed to fetch")));
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Can't reach the server");
  });

  it("explains the empty state", async () => {
    mockFetch(jsonResponse([]));
    render(<App />);
    expect(await screen.findByText("Start one with “New conversation”.")).toBeInTheDocument();
  });

  it("shows progress while creating a conversation and blocks double clicks", async () => {
    const created = deferred<undefined>();
    let posts = 0;
    routeFetch({
      "GET /api/conversations": () => jsonResponse([]),
      "POST /api/conversations": () => {
        posts += 1;
        return heldJson(created.promise, conv, 201);
      },
      "GET /api/messages/root/path": () => jsonResponse([root]),
      "GET /api/conversations/c1/tree": () => jsonResponse({ conversation_id: "c1", nodes: [treeNode(root)] }),
    });
    const ui = userEvent.setup();
    render(<App />);
    await screen.findByText("No conversations yet.");
    await ui.click(screen.getByRole("button", { name: "New conversation" }));
    const creating = screen.getByRole("button", { name: "Creating…" });
    expect(creating).toBeDisabled();
    fireEvent.click(creating);
    created.resolve(undefined);
    expect(await screen.findByText("Send a message to start.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New conversation" })).toBeEnabled();
    expect(posts).toBe(1);
  });

  it("names the action that failed and can be dismissed", async () => {
    routeFetch({
      "GET /api/conversations": () => jsonResponse([conv]),
      "GET /api/conversations/c1/tree": () => jsonResponse({ detail: "Conversation not found" }, 404),
    });
    const ui = userEvent.setup();
    render(<App />);
    await ui.click(await screen.findByRole("button", { name: "First" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent('Couldn\'t open "First": Conversation not found');
    await ui.click(within(alert).getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows a loading status while opening a conversation", async () => {
    const tree = deferred<undefined>();
    routeFetch({
      "GET /api/conversations": () => jsonResponse([conv]),
      "GET /api/conversations/c1/tree": () =>
        heldJson(tree.promise, { conversation_id: "c1", nodes: [treeNode(root), treeNode(u1), treeNode(a1)] }),
      "GET /api/messages/a1/path": () => jsonResponse([root, u1, a1]),
    });
    const ui = userEvent.setup();
    render(<App />);
    const button = await screen.findByRole("button", { name: "First" });
    await ui.click(button);
    expect(screen.getByRole("status")).toHaveTextContent("Opening conversation…");
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button).toBeDisabled();
    tree.resolve(undefined);
    expect(await screen.findByRole("list", { name: "Messages" })).toHaveTextContent("What is a CTE?");
    expect(screen.queryByText("Opening conversation…")).not.toBeInTheDocument();
  });
});

describe("TreeSidebar error states", () => {
  const nodes = [treeNode(root), treeNode(u1), treeNode(a1)];

  it("retries a failed initial load", async () => {
    mockFetch(jsonResponse({ detail: "boom" }, 500), jsonResponse({ conversation_id: "c1", nodes }));
    render(<TreeSidebar conversationId="c1" activeLeaf="a1" onSelect={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load the tree: boom");
    await userEvent.setup().click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("tree", { name: "Conversation tree" })).toHaveTextContent("What is a CTE?");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps the last tree when a refresh fails, with a retry", async () => {
    const u2 = msg({ id: "u2", parent_id: "a1", content: "Recursive?" });
    const a2 = msg({ id: "a2", parent_id: "u2", role: "assistant", content: "Yes." });
    mockFetch(
      jsonResponse({ conversation_id: "c1", nodes }),
      jsonResponse({ detail: "boom" }, 500),
      jsonResponse({ conversation_id: "c1", nodes: [...nodes, treeNode(u2), treeNode(a2)] }),
    );
    const { rerender } = render(<TreeSidebar conversationId="c1" activeLeaf="a1" onSelect={vi.fn()} />);
    await screen.findByRole("tree");
    // A reply created a2, which this tree hasn't seen yet → refetch, which fails.
    rerender(<TreeSidebar conversationId="c1" activeLeaf="a2" onSelect={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't refresh the tree: boom");
    expect(screen.getByRole("tree")).toHaveTextContent("What is a CTE?");
    await userEvent.setup().click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.getByRole("tree")).toHaveTextContent("Recursive?"));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("ChatView error states and validation", () => {
  it("retries a failed path load", async () => {
    mockFetch(jsonResponse({ detail: "boom" }, 500), jsonResponse([root, u1, a1]));
    render(<ChatView leafId="a1" onLeafChange={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load this conversation: boom");
    await userEvent.setup().click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("What is a CTE?")).toBeInTheDocument();
  });

  it("shows the rate-limit hint when a reply is throttled, and keeps the draft", async () => {
    mockFetch(
      jsonResponse([root, u1, a1]),
      new Response(JSON.stringify({ detail: "Rate limit exceeded" }), {
        status: 429,
        headers: { "Retry-After": "12" },
      }),
    );
    const ui = userEvent.setup();
    render(<ChatView leafId="a1" onLeafChange={vi.fn()} />);
    await screen.findByText("What is a CTE?");
    await ui.type(screen.getByRole("textbox", { name: "Message" }), "again{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("Reply failed: Rate limit exceeded. Try again in 12 s.");
    expect(screen.getByRole("textbox", { name: "Message" })).toHaveValue("again");
  });

  it("disables Send for whitespace-only drafts", async () => {
    const fetchMock = mockFetch(jsonResponse([root, u1, a1]));
    const ui = userEvent.setup();
    render(<ChatView leafId="a1" onLeafChange={vi.fn()} />);
    await screen.findByText("What is a CTE?");
    await ui.type(screen.getByRole("textbox", { name: "Message" }), "   {Enter}");
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("counts characters near the limit and blocks over-long messages", async () => {
    const fetchMock = mockFetch(jsonResponse([root, u1, a1]));
    render(<ChatView leafId="a1" onLeafChange={vi.fn()} />);
    await screen.findByText("What is a CTE?");
    const box = screen.getByRole("textbox", { name: "Message" });

    fireEvent.change(box, { target: { value: "short" } });
    expect(screen.queryByText(/characters/)).not.toBeInTheDocument();
    expect(box).not.toHaveAttribute("aria-invalid");

    fireEvent.change(box, { target: { value: "x".repeat(MAX_CONTENT_CHARS) } });
    expect(screen.getByText("50,000 / 50,000 characters")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send" })).toBeEnabled();

    fireEvent.change(box, { target: { value: "x".repeat(MAX_CONTENT_CHARS + 1) } });
    expect(screen.getByText("Message is too long: 50,001 / 50,000 characters.")).toBeInTheDocument();
    expect(box).toHaveAttribute("aria-invalid", "true");
    expect(box).toHaveAttribute("aria-describedby", "composer-count");
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    fireEvent.keyDown(box, { key: "Enter" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("marks the streaming reply busy and the Send button as in progress", async () => {
    const stream = controlledSSE();
    mockFetch(jsonResponse([root, u1, a1]), stream.response);
    const ui = userEvent.setup();
    render(<ChatView leafId="a1" onLeafChange={vi.fn()} />);
    await screen.findByText("What is a CTE?");
    await ui.type(screen.getByRole("textbox", { name: "Message" }), "more{Enter}");
    expect(await screen.findByRole("button", { name: "Sending…" })).toBeDisabled();
    const items = within(screen.getByRole("list", { name: "Messages" })).getAllByRole("listitem");
    expect(items.at(-1)).toHaveAttribute("aria-busy", "true");
    expect(items.at(-1)).toHaveTextContent("…");
    stream.close();
  });
});
