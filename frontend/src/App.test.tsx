import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App.tsx";
import type { Message, TreeNode } from "./api/types.ts";
import { jsonResponse, mockFetch, routeFetch } from "./test/fetch.ts";
import { msg } from "./test/messages.ts";

const treeNode = (m: Message): TreeNode => ({
  id: m.id,
  parent_id: m.parent_id,
  role: m.role,
  preview: m.content,
  created_at: m.created_at,
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("App", () => {
  it("renders the title and a loading state", () => {
    mockFetch(jsonResponse([]));
    render(<App />);
    expect(screen.getByRole("heading", { name: "Branch Chat" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
  });

  it("lists conversations from the API", async () => {
    const fetchMock = mockFetch(
      jsonResponse([
        { id: "c1", title: "First", created_at: "2026-10-28T00:00:00Z", root_message_id: "m1" },
        { id: "c2", title: "Second", created_at: "2026-10-28T00:01:00Z", root_message_id: "m2" },
      ]),
    );
    render(<App />);
    const list = await screen.findByRole("list", { name: "Conversations" });
    expect(list).toHaveTextContent("First");
    expect(list).toHaveTextContent("Second");
    expect(fetchMock).toHaveBeenCalledWith("/api/conversations", expect.anything());
  });

  it("shows an empty state", async () => {
    mockFetch(jsonResponse([]));
    render(<App />);
    expect(await screen.findByText("No conversations yet.")).toBeInTheDocument();
  });

  it("shows an error when the API fails", async () => {
    mockFetch(jsonResponse({ detail: "boom" }, 500));
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  });

  it("opens a conversation at its most recent node, with its tree", async () => {
    const root = msg({ id: "r1", role: "system", content: "sys" });
    const u = msg({ id: "u1", parent_id: "r1", content: "hello" });
    const a = msg({ id: "a1", parent_id: "u1", role: "assistant", content: "hi back" });
    const fetchMock = routeFetch({
      "GET /api/conversations": () =>
        jsonResponse([{ id: "c1", title: "First", created_at: root.created_at, root_message_id: "r1" }]),
      // Out of order on purpose: the newest node wins, not the last listed.
      "GET /api/conversations/c1/tree": () =>
        jsonResponse({ conversation_id: "c1", nodes: [treeNode(root), treeNode(a), treeNode(u)] }),
      "GET /api/messages/a1/path": () => jsonResponse([root, u, a]),
    });
    render(<App />);
    await userEvent.setup().click(await screen.findByRole("button", { name: "First" }));
    const chat = await screen.findByRole("region", { name: "Chat" });
    expect(await within(chat).findByText("hi back")).toBeInTheDocument();
    const tree = await screen.findByRole("tree", { name: "Conversation tree" });
    expect(within(tree).getByRole("treeitem", { selected: true })).toHaveTextContent("AI: hi back");
    expect(fetchMock).toHaveBeenCalledWith("/api/messages/a1/path", expect.anything());
  });

  it("clicking a tree node switches the chat to that branch", async () => {
    const root = msg({ id: "r1", role: "system", content: "sys" });
    const u = msg({ id: "u1", parent_id: "r1", content: "hello" });
    const a = msg({ id: "a1", parent_id: "u1", role: "assistant", content: "main answer" });
    const q = msg({ id: "q1", parent_id: "a1", content: "side question" });
    const b = msg({ id: "b1", parent_id: "q1", role: "assistant", content: "side answer" });
    const u2 = msg({ id: "u2", parent_id: "a1", content: "main follow-up" });
    const a2 = msg({ id: "a2", parent_id: "u2", role: "assistant", content: "main reply" });
    routeFetch({
      "GET /api/conversations": () =>
        jsonResponse([{ id: "c1", title: "First", created_at: root.created_at, root_message_id: "r1" }]),
      "GET /api/conversations/c1/tree": () =>
        jsonResponse({ conversation_id: "c1", nodes: [root, u, a, q, b, u2, a2].map(treeNode) }),
      "GET /api/messages/a2/path": () => jsonResponse([root, u, a, u2, a2]),
      "GET /api/messages/b1/path": () => jsonResponse([root, u, a, q, b]),
    });
    const ui = userEvent.setup();
    render(<App />);
    await ui.click(await screen.findByRole("button", { name: "First" }));
    const chat = await screen.findByRole("region", { name: "Chat" });
    await within(chat).findByText("main reply");

    await ui.click(await screen.findByRole("button", { name: "AI: side answer" }));
    // The chat pane re-mounts (loading → loaded), so re-query it inside waitFor.
    const chatPane = () => screen.getByRole("region", { name: "Chat" });
    await waitFor(() => expect(within(chatPane()).getByText("side answer")).toBeInTheDocument());
    expect(within(chatPane()).queryByText("main reply")).not.toBeInTheDocument();
    expect(screen.getByRole("treeitem", { selected: true })).toHaveTextContent("AI: side answer");
  });

  it("creates a new conversation and opens its empty chat", async () => {
    const root = msg({ id: "r9", role: "system", content: "sys" });
    routeFetch({
      "GET /api/conversations": () => jsonResponse([]),
      "POST /api/conversations": () =>
        jsonResponse({ id: "c9", title: "New conversation", created_at: root.created_at, root_message_id: "r9" }, 201),
      "GET /api/messages/r9/path": () => jsonResponse([root]),
      "GET /api/conversations/c9/tree": () => jsonResponse({ conversation_id: "c9", nodes: [treeNode(root)] }),
    });
    render(<App />);
    await screen.findByText("No conversations yet.");
    await userEvent.setup().click(screen.getByRole("button", { name: "New conversation" }));
    expect(await screen.findByText("Send a message to start.")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Conversations" })).toHaveTextContent("New conversation");
    expect(await screen.findByText("No messages yet.")).toBeInTheDocument();
  });
});
