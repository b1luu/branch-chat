import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App.tsx";
import { fetchCall, jsonResponse, mockFetch } from "./test/fetch.ts";
import { msg } from "./test/messages.ts";

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

  it("opens a conversation at its most recent node", async () => {
    const root = msg({ id: "r1", role: "system", content: "sys" });
    const u = msg({ id: "u1", parent_id: "r1", content: "hello" });
    const a = msg({ id: "a1", parent_id: "u1", role: "assistant", content: "hi back" });
    const node = (m: typeof root) => ({ id: m.id, parent_id: m.parent_id, role: m.role, preview: m.content, created_at: m.created_at });
    const fetchMock = mockFetch(
      jsonResponse([{ id: "c1", title: "First", created_at: root.created_at, root_message_id: "r1" }]),
      jsonResponse({ conversation_id: "c1", nodes: [node(root), node(a), node(u)] }),
      jsonResponse([root, u, a]),
    );
    render(<App />);
    await userEvent.setup().click(await screen.findByRole("button", { name: "First" }));
    expect(await screen.findByText("hi back")).toBeInTheDocument();
    expect(fetchCall(fetchMock, 1)[0]).toBe("/api/conversations/c1/tree");
    expect(fetchCall(fetchMock, 2)[0]).toBe("/api/messages/a1/path");
  });

  it("creates a new conversation and opens its empty chat", async () => {
    const root = msg({ id: "r9", role: "system", content: "sys" });
    mockFetch(
      jsonResponse([]),
      jsonResponse({ id: "c9", title: "New conversation", created_at: root.created_at, root_message_id: "r9" }, 201),
      jsonResponse([root]),
    );
    render(<App />);
    await screen.findByText("No conversations yet.");
    await userEvent.setup().click(screen.getByRole("button", { name: "New conversation" }));
    expect(await screen.findByText("Send a message to start.")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Conversations" })).toHaveTextContent("New conversation");
  });
});
