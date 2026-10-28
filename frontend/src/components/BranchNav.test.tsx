import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchCall, jsonResponse, mockFetch } from "../test/fetch.ts";
import type { TreeNode } from "../api/types.ts";
import BranchNav from "./BranchNav.tsx";

let t = 0;
function node(id: string, parent_id: string | null, role: TreeNode["role"], preview = id): TreeNode {
  t += 1;
  return { id, parent_id, role, preview, created_at: new Date(Date.UTC(2026, 0, 1, 0, 0, t)).toISOString() };
}

// root → u1 → a1 ─┬→ u2 → a2 → u6 → a6                       (main thread)
//                 └→ q1 → b1 ─┬→ q2 → b2                     (side branch)
//                             └→ r1 → c1                     (nested side branch)
const nodes = [
  node("root", null, "system"),
  node("u1", "root", "user"),
  node("a1", "u1", "assistant"),
  node("u2", "a1", "user"),
  node("a2", "u2", "assistant"),
  node("q1", "a1", "user", "Why is it called   a common\ntable expression at all?"),
  node("b1", "q1", "assistant"),
  node("q2", "b1", "user"),
  node("b2", "q2", "assistant"),
  node("r1", "b1", "user", "Show an example"),
  node("c1", "r1", "assistant"),
  node("u6", "a2", "user"),
  node("a6", "u6", "assistant"),
];

afterEach(() => {
  vi.unstubAllGlobals();
});

function crumbs() {
  return within(screen.getByRole("navigation", { name: "Breadcrumb" }))
    .getAllByRole("listitem")
    .map((li) => li.textContent);
}

describe("BranchNav", () => {
  it("renders nothing on the main thread", () => {
    const { container } = render(<BranchNav nodes={nodes} activeLeaf="a2" onSelect={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing while the leaf isn't in the tree yet", () => {
    const { container } = render(<BranchNav nodes={nodes} activeLeaf="fresh" onSelect={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows main thread › side branch, with the current crumb marked", () => {
    render(<BranchNav nodes={nodes} activeLeaf="b2" onSelect={vi.fn()} />);
    expect(crumbs()).toEqual(["Main thread", "Why is it called a common table…"]);
    expect(screen.getByText("Why is it called a common table…")).toHaveAttribute("aria-current", "location");
  });

  it("Back to main thread goes to the tip of the main thread, past the fork point", async () => {
    const onSelect = vi.fn();
    render(<BranchNav nodes={nodes} activeLeaf="b2" onSelect={onSelect} />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Back to main thread" }));
    // The main thread grew to a6 after the branch was made; we land on its newest turn.
    expect(onSelect).toHaveBeenCalledWith("a6");
  });

  it("from a nested branch, back goes to the parent branch's tip and crumbs jump further", async () => {
    const onSelect = vi.fn();
    const ui = userEvent.setup();
    render(<BranchNav nodes={nodes} activeLeaf="c1" onSelect={onSelect} />);
    expect(crumbs()).toEqual(["Main thread", "Why is it called a common table…", "Show an example"]);

    await ui.click(screen.getByRole("button", { name: "Back to parent branch" }));
    expect(onSelect).toHaveBeenLastCalledWith("b2");

    await ui.click(screen.getByRole("button", { name: "Main thread" }));
    expect(onSelect).toHaveBeenLastCalledWith("a6");

    await ui.click(screen.getByRole("button", { name: "Why is it called a common table…" }));
    expect(onSelect).toHaveBeenLastCalledWith("b2");
  });

  it("works when the active leaf is the fork's own first node", () => {
    render(<BranchNav nodes={nodes} activeLeaf="q1" onSelect={vi.fn()} />);
    expect(crumbs()).toEqual(["Main thread", "Why is it called a common table…"]);
  });

  it("Merge back summarizes the branch, then returns to the parent branch's tip", async () => {
    let resolve: (r: Response) => void = () => {};
    const fetchMock = vi.fn<typeof fetch>(() => new Promise<Response>((r) => (resolve = r)));
    vi.stubGlobal("fetch", fetchMock);
    const onSelect = vi.fn();
    const ui = userEvent.setup();
    render(<BranchNav nodes={nodes} activeLeaf="b2" onSelect={onSelect} />);

    await ui.click(screen.getByRole("button", { name: "Merge back" }));
    expect(screen.getByRole("button", { name: "Merging…" })).toBeDisabled();
    expect(fetchCall(fetchMock)[0]).toBe("/api/branches/b2/merge");
    expect(onSelect).not.toHaveBeenCalled();

    resolve(jsonResponse({ id: "n1", message_id: "a1", source_leaf_id: "b2", content: "S", created_at: "x" }, 201));
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith("a6"));
  });

  it("merging a nested branch returns to its parent branch", async () => {
    mockFetch(jsonResponse({ id: "n1", message_id: "b1", source_leaf_id: "c1", content: "S", created_at: "x" }, 201));
    const onSelect = vi.fn();
    render(<BranchNav nodes={nodes} activeLeaf="c1" onSelect={onSelect} />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Merge back" }));
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith("b2"));
  });

  it("shows a merge error and stays on the branch", async () => {
    mockFetch(jsonResponse({ detail: "LLM provider error: timeout" }, 502));
    const onSelect = vi.fn();
    render(<BranchNav nodes={nodes} activeLeaf="b2" onSelect={onSelect} />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Merge back" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Merge failed: LLM provider error: timeout");
    expect(screen.getByRole("button", { name: "Merge back" })).toBeEnabled();
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("offers no merge on the main thread", () => {
    render(<BranchNav nodes={nodes} activeLeaf="a6" onSelect={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Merge back" })).not.toBeInTheDocument();
  });
});
