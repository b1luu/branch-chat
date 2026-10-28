import { describe, expect, it } from "vitest";
import type { TreeNode } from "./api/types.ts";
import { childrenIndex, flattenTree, pathIds } from "./tree.ts";

let t = 0;
function node(id: string, parent_id: string | null, role: TreeNode["role"] = "user"): TreeNode {
  t += 1;
  return { id, parent_id, role, preview: id, created_at: new Date(Date.UTC(2026, 0, 1, 0, 0, t)).toISOString() };
}

// root(system) → u1 → a1 ─┬→ u2 → a2         (main line)
//                         └→ q1 → b1 ─┬→ q2   (side branch with its own fork)
//                                     └→ q3
const nodes = [
  node("root", null, "system"),
  node("u1", "root"),
  node("a1", "u1", "assistant"),
  node("u2", "a1"),
  node("a2", "u2", "assistant"),
  node("q1", "a1"),
  node("b1", "q1", "assistant"),
  node("q2", "b1"),
  node("q3", "b1"),
];

const shape = (rows: ReturnType<typeof flattenTree>) => rows.map((r) => `${r.node.id}@${r.depth}${r.forkSize ? `/${r.forkSize}` : ""}`);

describe("tree helpers", () => {
  it("indexes children oldest first", () => {
    const shuffled = [...nodes].reverse();
    expect(childrenIndex(shuffled).get("a1")?.map((n) => n.id)).toEqual(["u2", "q1"]);
  });

  it("computes the root→leaf path", () => {
    expect([...pathIds(nodes, "q3")].sort()).toEqual(["a1", "b1", "q1", "q3", "root", "u1"]);
    expect(pathIds(nodes, "missing").size).toBe(0);
    expect(pathIds(nodes, null).size).toBe(0);
  });

  it("keeps chains flat and indents only at forks, hiding the system root", () => {
    expect(shape(flattenTree(nodes, new Set()))).toEqual([
      "u1@0",
      "a1@0/2",
      "u2@1",
      "a2@1",
      "q1@1",
      "b1@1/2",
      "q2@2",
      "q3@2",
    ]);
  });

  it("collapsing a fork hides everything below it", () => {
    expect(shape(flattenTree(nodes, new Set(["a1"])))).toEqual(["u1@0", "a1@0/2"]);
    expect(shape(flattenTree(nodes, new Set(["b1"])))).toEqual(["u1@0", "a1@0/2", "u2@1", "a2@1", "q1@1", "b1@1/2"]);
  });

  it("indents multiple first turns under the hidden root", () => {
    const forkAtRoot = [node("r", null, "system"), node("x", "r"), node("y", "r")];
    expect(shape(flattenTree(forkAtRoot, new Set()))).toEqual(["x@1", "y@1"]);
  });

  it("handles a very deep chain without recursion", () => {
    const deep: TreeNode[] = [node("n0", null)];
    for (let i = 1; i < 20_000; i++) deep.push(node(`n${i}`, `n${i - 1}`));
    const rows = flattenTree(deep, new Set());
    expect(rows).toHaveLength(20_000);
    expect(rows.every((r) => r.depth === 0)).toBe(true);
  });
});
