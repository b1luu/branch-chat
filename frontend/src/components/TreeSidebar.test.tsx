import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { TreeNode } from "../api/types.ts";
import { fetchCall, jsonResponse, mockFetch } from "../test/fetch.ts";
import TreeSidebar from "./TreeSidebar.tsx";

afterEach(() => {
  vi.unstubAllGlobals();
});

let t = 0;
function node(id: string, parent_id: string | null, role: TreeNode["role"], preview: string): TreeNode {
  t += 1;
  return { id, parent_id, role, preview, created_at: new Date(Date.UTC(2026, 0, 1, 0, 0, t)).toISOString() };
}

const nodes = [
  node("root", null, "system", "You are a helpful assistant."),
  node("u1", "root", "user", "What is a CTE?"),
  node("a1", "u1", "assistant", "A common table expression."),
  node("u2", "a1", "user", "Recursive?"),
  node("a2", "u2", "assistant", "Yes."),
  node("q1", "a1", "user", "Side question"),
  node("b1", "q1", "assistant", "Side answer"),
];
const tree = (ns: TreeNode[] = nodes) => jsonResponse({ conversation_id: "c1", nodes: ns });

function items() {
  return within(screen.getByRole("tree", { name: "Conversation tree" })).getAllByRole("treeitem");
}
/** The node label of a tree item (ignores the collapse toggle). */
const label = (li: HTMLElement) => li.querySelector(".tree-label")?.textContent;
const item = (text: string) => {
  const found = items().find((li) => li.textContent?.includes(text));
  if (!found) throw new Error(`no tree item containing ${text}`);
  return found;
};

describe("TreeSidebar", () => {
  it("renders the tree with role labels, hiding the system root", async () => {
    const fetchMock = mockFetch(tree());
    render(<TreeSidebar conversationId="c1" activeLeaf="a2" onSelect={vi.fn()} />);
    await screen.findByRole("tree");
    expect(items().map(label)).toEqual([
      "You: What is a CTE?",
      "AI: A common table expression.",
      "You: Recursive?",
      "AI: Yes.",
      "You: Side question",
      "AI: Side answer",
    ]);
    expect(screen.queryByText(/helpful assistant/)).not.toBeInTheDocument();
    expect(fetchCall(fetchMock)[0]).toBe("/api/conversations/c1/tree");
    expect(item("Side question")).toHaveAttribute("aria-level", "2");
    expect(item("What is a CTE?")).toHaveAttribute("aria-level", "1");
  });

  it("highlights the active path and selects the active leaf", async () => {
    mockFetch(tree());
    render(<TreeSidebar conversationId="c1" activeLeaf="b1" onSelect={vi.fn()} />);
    await screen.findByRole("tree");
    const onPath = items().filter((li) => li.dataset.onPath === "true").map(label);
    expect(onPath).toEqual([
      "You: What is a CTE?",
      "AI: A common table expression.",
      "You: Side question",
      "AI: Side answer",
    ]);
    expect(item("Side answer")).toHaveAttribute("aria-selected", "true");
    expect(item("Yes.")).toHaveAttribute("aria-selected", "false");
    expect(within(item("Side answer")).getByRole("button", { current: true })).toBeInTheDocument();
  });

  it("calls onSelect with the clicked node", async () => {
    mockFetch(tree());
    const onSelect = vi.fn();
    render(<TreeSidebar conversationId="c1" activeLeaf="a2" onSelect={onSelect} />);
    await screen.findByRole("tree");
    await userEvent.setup().click(screen.getByRole("button", { name: "AI: Side answer" }));
    expect(onSelect).toHaveBeenCalledWith("b1");
  });

  it("collapses and expands a fork", async () => {
    mockFetch(tree());
    const ui = userEvent.setup();
    render(<TreeSidebar conversationId="c1" activeLeaf="a2" onSelect={vi.fn()} />);
    await screen.findByRole("tree");
    expect(item("A common table")).toHaveAttribute("aria-expanded", "true");

    await ui.click(screen.getByRole("button", { name: "Collapse 2 branches" }));
    expect(items()).toHaveLength(2);
    expect(item("A common table")).toHaveAttribute("aria-expanded", "false");

    await ui.click(screen.getByRole("button", { name: "Expand 2 branches" }));
    expect(items()).toHaveLength(6);
  });

  it("refetches when the active leaf is a node it hasn't seen", async () => {
    const extra = node("a3", "b1", "assistant", "New reply");
    const fetchMock = mockFetch(tree(), tree([...nodes, node("u3", "b1", "user", "More?"), extra]));
    const { rerender } = render(<TreeSidebar conversationId="c1" activeLeaf="b1" onSelect={vi.fn()} />);
    await screen.findByRole("tree");

    rerender(<TreeSidebar conversationId="c1" activeLeaf="a2" onSelect={vi.fn()} />);
    expect(fetchMock).toHaveBeenCalledTimes(1); // known node: no fetch

    rerender(<TreeSidebar conversationId="c1" activeLeaf="a3" onSelect={vi.fn()} />);
    await waitFor(() => expect(item("New reply")).toHaveAttribute("aria-selected", "true"));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("refetches when the conversation changes", async () => {
    const other = [node("r2", null, "system", "sys"), node("x1", "r2", "user", "Other chat")];
    mockFetch(tree(), jsonResponse({ conversation_id: "c2", nodes: other }));
    const { rerender } = render(<TreeSidebar conversationId="c1" activeLeaf="a2" onSelect={vi.fn()} />);
    await screen.findByRole("tree");
    rerender(<TreeSidebar conversationId="c2" activeLeaf="x1" onSelect={vi.fn()} />);
    expect(await screen.findByText(/Other chat/)).toBeInTheDocument();
    expect(screen.queryByText(/Recursive/)).not.toBeInTheDocument();
  });

  it("shows empty and error states", async () => {
    mockFetch(tree([node("r", null, "system", "sys")]));
    const { unmount } = render(<TreeSidebar conversationId="c1" activeLeaf="r" onSelect={vi.fn()} />);
    expect(await screen.findByText("No messages yet.")).toBeInTheDocument();
    unmount();

    mockFetch(jsonResponse({ detail: "Conversation not found" }, 404));
    render(<TreeSidebar conversationId="zz" activeLeaf={null} onSelect={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Conversation not found");
  });
});
