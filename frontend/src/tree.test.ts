import { describe, expect, it } from "vitest";
import type { TreeNode } from "./api/types.ts";
import { branchSegments, branchTip, childrenIndex, compareCreated, flattenTree, pathIds } from "./tree.ts";

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

  it("marks rows off the main thread as side branches, including forks inside them", () => {
    const side = flattenTree(nodes, new Set())
      .filter((r) => r.side)
      .map((r) => r.node.id);
    expect(side).toEqual(["q1", "b1", "q2", "q3"]);
    const forkAtRoot = [node("r", null, "system"), node("x", "r"), node("y", "r")];
    expect(flattenTree(forkAtRoot, new Set()).map((r) => [r.node.id, r.side])).toEqual([
      ["x", false],
      ["y", true],
    ]);
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

describe("branchSegments", () => {
  const ids = (leaf: string) => branchSegments(nodes, leaf).map((s) => [s.start.id, s.end.id]);

  it("is a single main segment on the main line", () => {
    expect(ids("a2")).toEqual([["root", "a2"]]);
    expect(ids("u1")).toEqual([["root", "u1"]]);
  });

  it("starts a new segment at each later sibling", () => {
    expect(ids("b1")).toEqual([["root", "a1"], ["q1", "b1"]]);
    // q2 is b1's oldest child, so it continues the side branch; q3 forks again.
    expect(ids("q2")).toEqual([["root", "a1"], ["q1", "q2"]]);
    expect(ids("q3")).toEqual([["root", "a1"], ["q1", "b1"], ["q3", "q3"]]);
  });

  it("is empty for an unknown or missing leaf", () => {
    expect(branchSegments(nodes, "nope")).toEqual([]);
    expect(branchSegments(nodes, null)).toEqual([]);
  });

  it("treats a later sibling of the very first turn as a side branch", () => {
    const forked = [...nodes, node("u9", "root")];
    expect(branchSegments(forked, "u9").map((s) => s.start.id)).toEqual(["root", "u9"]);
  });
});

describe("branchTip", () => {
  it("follows the oldest child to the end of the branch", () => {
    expect(branchTip(nodes, "a1")).toBe("a2");
    expect(branchTip(nodes, "root")).toBe("a2");
    expect(branchTip(nodes, "b1")).toBe("q2");
  });

  it("returns the node itself when it is a leaf or unknown", () => {
    expect(branchTip(nodes, "a2")).toBe("a2");
    expect(branchTip(nodes, "nope")).toBe("nope");
  });

  it("handles very deep chains without recursion", () => {
    const chain: TreeNode[] = [node("n0", null)];
    for (let i = 1; i < 20_000; i++) chain.push(node(`n${i}`, `n${i - 1}`));
    expect(branchTip(chain, "n0")).toBe("n19999");
  });
});

describe("created_at ordering (matches the server's ORDER BY created_at, id)", () => {
  const at = (id: string, parent_id: string | null, created_at: string): TreeNode => ({
    id,
    parent_id,
    role: "user",
    preview: id,
    created_at,
  });

  it("orders a whole-second timestamp (no fraction) before a later fractional one", () => {
    // The API drops the fraction when it is exactly zero; as strings "42.5Z" < "42Z".
    const kids = [at("later", "p", "2026-01-01T00:00:42.500000Z"), at("first", "p", "2026-01-01T00:00:42Z")];
    expect(childrenIndex(kids).get("p")?.map((n) => n.id)).toEqual(["first", "later"]);
  });

  it("keeps microsecond precision that Date.parse would round away", () => {
    const kids = [at("b", "p", "2026-01-01T00:00:42.123457Z"), at("a", "p", "2026-01-01T00:00:42.123456Z")];
    expect(childrenIndex(kids).get("p")?.map((n) => n.id)).toEqual(["a", "b"]);
  });

  it("compares instants, not wall-clock text, across offsets", () => {
    const utc = at("utc", "p", "2026-01-01T00:30:00Z");
    const plusTwo = at("plus2", "p", "2026-01-01T02:00:00+02:00"); // 00:00Z, earlier
    expect(compareCreated(plusTwo, utc)).toBeLessThan(0);
    expect(compareCreated(utc, plusTwo)).toBeGreaterThan(0);
  });

  it("breaks exact ties by id, like the server", () => {
    const ts = "2026-01-01T00:00:42.100000Z";
    const kids = [at("bb", "p", ts), at("aa", "p", ts)];
    expect(childrenIndex(kids).get("p")?.map((n) => n.id)).toEqual(["aa", "bb"]);
    expect(compareCreated(at("aa", "p", ts), at("aa", "p", ts))).toBe(0);
  });

  it("picks the right main thread when the oldest child landed on a whole second", () => {
    const nodes = [
      at("root", null, "2026-01-01T00:00:40.250000Z"),
      at("side", "root", "2026-01-01T00:00:42.900000Z"),
      at("main", "root", "2026-01-01T00:00:42Z"),
    ];
    expect(branchSegments(nodes, "main").map((s) => s.start.id)).toEqual(["root"]);
    expect(branchSegments(nodes, "side").map((s) => s.start.id)).toEqual(["root", "side"]);
    expect(branchTip(nodes, "root")).toBe("main");
  });
});
