import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App.tsx";
import type { Message, TreeNode } from "./api/types.ts";
import { controlledSSE, jsonResponse, mockFetch, routeFetch, sseEvent } from "./test/fetch.ts";
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

  it("shows a breadcrumb on a side branch and Back to main thread returns to the main tip", async () => {
    const root = msg({ id: "r1", role: "system", content: "sys" });
    const u = msg({ id: "u1", parent_id: "r1", content: "hello" });
    const a = msg({ id: "a1", parent_id: "u1", role: "assistant", content: "main answer" });
    const u2 = msg({ id: "u2", parent_id: "a1", content: "main follow-up" });
    const a2 = msg({ id: "a2", parent_id: "u2", role: "assistant", content: "main reply" });
    // Created after the main follow-up, so this is the side branch (and the newest node).
    const q = msg({ id: "q1", parent_id: "a1", content: "side question", quote: "answer" });
    const b = msg({ id: "b1", parent_id: "q1", role: "assistant", content: "side answer" });
    routeFetch({
      "GET /api/conversations": () =>
        jsonResponse([{ id: "c1", title: "First", created_at: root.created_at, root_message_id: "r1" }]),
      "GET /api/conversations/c1/tree": () =>
        jsonResponse({ conversation_id: "c1", nodes: [root, u, a, u2, a2, q, b].map(treeNode) }),
      "GET /api/messages/a2/path": () => jsonResponse([root, u, a, u2, a2]),
      "GET /api/messages/b1/path": () => jsonResponse([root, u, a, q, b]),
    });
    const ui = userEvent.setup();
    render(<App />);
    await ui.click(await screen.findByRole("button", { name: "First" }));
    const chatPane = () => screen.getByRole("region", { name: "Chat" });
    await waitFor(() => expect(within(chatPane()).getByText("side answer")).toBeInTheDocument());

    const crumbs = await screen.findByRole("navigation", { name: "Breadcrumb" });
    expect(crumbs).toHaveTextContent("Main thread");
    expect(crumbs).toHaveTextContent("side question");

    await ui.click(screen.getByRole("button", { name: "Back to main thread" }));
    await waitFor(() => expect(within(chatPane()).getByText("main reply")).toBeInTheDocument());
    expect(screen.queryByRole("navigation", { name: "Breadcrumb" })).not.toBeInTheDocument();
    expect(screen.getByRole("treeitem", { selected: true })).toHaveTextContent("AI: main reply");
  });

  it("merging a side branch returns to the main thread and shows the note", async () => {
    const root = msg({ id: "r1", role: "system", content: "sys" });
    const u = msg({ id: "u1", parent_id: "r1", content: "hello" });
    const a = msg({ id: "a1", parent_id: "u1", role: "assistant", content: "main answer" });
    const u2 = msg({ id: "u2", parent_id: "a1", content: "main follow-up" });
    const a2 = msg({ id: "a2", parent_id: "u2", role: "assistant", content: "main reply" });
    const q = msg({ id: "q1", parent_id: "a1", content: "side question" });
    const b = msg({ id: "b1", parent_id: "q1", role: "assistant", content: "side answer" });
    const note = { id: "n1", message_id: "a1", source_leaf_id: "b1", content: "Side said: yes.", created_at: b.created_at };
    const fetchMock = routeFetch({
      "GET /api/conversations": () =>
        jsonResponse([{ id: "c1", title: "First", created_at: root.created_at, root_message_id: "r1" }]),
      "GET /api/conversations/c1/tree": () =>
        jsonResponse({ conversation_id: "c1", nodes: [root, u, a, u2, a2, q, b].map(treeNode) }),
      "GET /api/messages/b1/path": () => jsonResponse([root, u, a, q, b]),
      "POST /api/branches/b1/merge": () => jsonResponse(note, 201),
      "GET /api/messages/a2/path": () => jsonResponse([root, u, { ...a, notes: [note] }, u2, a2]),
    });
    const ui = userEvent.setup();
    render(<App />);
    await ui.click(await screen.findByRole("button", { name: "First" }));
    const chatPane = () => screen.getByRole("region", { name: "Chat" });
    await waitFor(() => expect(within(chatPane()).getByText("side answer")).toBeInTheDocument());

    await ui.click(await screen.findByRole("button", { name: "Merge back" }));
    await waitFor(() => expect(within(chatPane()).getByText("main reply")).toBeInTheDocument());
    expect(within(chatPane()).getByRole("complementary", { name: "Merged note" })).toHaveTextContent("Side said: yes.");
    expect(fetchMock).toHaveBeenCalledWith("/api/branches/b1/merge", expect.objectContaining({ method: "POST" }));
  });

  describe("a reply that finishes after navigating away (Q4)", () => {
    const root = msg({ id: "r1", role: "system", content: "sys" });
    const u = msg({ id: "u1", parent_id: "r1", content: "hello" });
    const a = msg({ id: "a1", parent_id: "u1", role: "assistant", content: "main answer" });
    const q = msg({ id: "q1", parent_id: "a1", content: "side question" });
    const b = msg({ id: "b1", parent_id: "q1", role: "assistant", content: "side answer" });
    const u2 = msg({ id: "u2", parent_id: "a1", content: "main follow-up" });
    const a2 = msg({ id: "a2", parent_id: "u2", role: "assistant", content: "main reply" });
    const u3 = msg({ id: "u3", parent_id: "a2", content: "slow question" });
    const a3 = msg({ id: "a3", parent_id: "u3", role: "assistant", content: "slow answer" });
    const otherRoot = msg({ id: "r2", conversation_id: "c2", role: "system", content: "sys" });
    const x = msg({ id: "x1", conversation_id: "c2", parent_id: "r2", role: "assistant", content: "other chat" });

    function setup() {
      const stream = controlledSSE();
      // What the server has persisted so far; the background reply adds to it.
      const c1Nodes = [root, u, a, q, b, u2, a2];
      const fetchMock = routeFetch({
        "GET /api/conversations": () =>
          jsonResponse([
            { id: "c1", title: "First", created_at: root.created_at, root_message_id: "r1" },
            { id: "c2", title: "Second", created_at: otherRoot.created_at, root_message_id: "r2" },
          ]),
        "GET /api/conversations/c1/tree": () => jsonResponse({ conversation_id: "c1", nodes: c1Nodes.map(treeNode) }),
        "GET /api/conversations/c2/tree": () =>
          jsonResponse({ conversation_id: "c2", nodes: [otherRoot, x].map(treeNode) }),
        "GET /api/messages/a2/path": () => jsonResponse([root, u, a, u2, a2]),
        "GET /api/messages/b1/path": () => jsonResponse([root, u, a, q, b]),
        "GET /api/messages/x1/path": () => jsonResponse([otherRoot, x]),
        "GET /api/messages/a3/path": () => jsonResponse([root, u, a, u2, a2, u3, a3]),
        "POST /api/messages/a2/reply": () => stream.response,
      });
      const finish = () => {
        c1Nodes.push(u3, a3);
        stream.push(sseEvent("user", u3));
        stream.push(sseEvent("token", { text: "slow answer" }));
        stream.push(sseEvent("done", a3));
        stream.close();
      };
      const treeFetches = (id: string) =>
        fetchMock.mock.calls.filter(([url]) => url === `/api/conversations/${id}/tree`).length;
      return { fetchMock, finish, treeFetches };
    }

    async function sendOnMainThread() {
      const ui = userEvent.setup();
      render(<App />);
      // Newest node is a2, so the conversation opens on the main thread.
      await ui.click(await screen.findByRole("button", { name: "First" }));
      const chatPane = () => screen.getByRole("region", { name: "Chat" });
      await waitFor(() => expect(within(chatPane()).getByText("main reply")).toBeInTheDocument());
      await ui.type(screen.getByRole("textbox", { name: "Message" }), "slow question{Enter}");
      await screen.findByRole("button", { name: "Sending…" });
      return { ui, chatPane };
    }

    it("adds the background reply to the tree without leaving the branch the user moved to", async () => {
      const { finish, treeFetches } = setup();
      const { ui, chatPane } = await sendOnMainThread();

      await ui.click(screen.getByRole("button", { name: "AI: side answer" }));
      await waitFor(() => expect(within(chatPane()).getByText("side answer")).toBeInTheDocument());
      const before = treeFetches("c1");
      finish();

      const tree = await screen.findByRole("tree", { name: "Conversation tree" });
      expect(await within(tree).findByRole("button", { name: "AI: slow answer" })).toBeInTheDocument();
      expect(within(tree).getByRole("button", { name: "You: slow question" })).toBeInTheDocument();
      expect(treeFetches("c1")).toBe(before + 1);
      // The user stays where they navigated to.
      expect(screen.getByRole("treeitem", { selected: true })).toHaveTextContent("AI: side answer");
      expect(within(chatPane()).queryByText("slow answer")).not.toBeInTheDocument();
    });

    it("doesn't refetch the open conversation's tree for a reply in another conversation", async () => {
      const { finish, treeFetches } = setup();
      const { ui, chatPane } = await sendOnMainThread();

      await ui.click(screen.getByRole("button", { name: "Second" }));
      await waitFor(() => expect(within(chatPane()).getByText("other chat")).toBeInTheDocument());
      await screen.findByRole("button", { name: "AI: other chat" });
      const c1Before = treeFetches("c1");
      const c2Before = treeFetches("c2");
      finish();

      await new Promise((r) => setTimeout(r, 30));
      expect(treeFetches("c2")).toBe(c2Before);
      expect(treeFetches("c1")).toBe(c1Before);
      expect(screen.getByRole("treeitem", { selected: true })).toHaveTextContent("AI: other chat");

      // Back in the first conversation, the reply is there (it's now the newest node).
      await ui.click(screen.getByRole("button", { name: "First" }));
      await waitFor(() => expect(within(chatPane()).getByText("slow answer")).toBeInTheDocument());
    });
  });
});
