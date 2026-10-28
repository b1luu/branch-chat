/**
 * Pure helpers for turning the flat `/tree` node list into what the sidebar draws.
 * The backend sends nodes parents-first with `parent_id` links (adjacency list);
 * the client assembles the tree.
 */
import type { TreeNode } from "./api/types.ts";

/** Children of each node id (`null` = roots), oldest first. */
export function childrenIndex(nodes: readonly TreeNode[]): Map<string | null, TreeNode[]> {
  const index = new Map<string | null, TreeNode[]>();
  for (const n of nodes) {
    const list = index.get(n.parent_id);
    if (list) list.push(n);
    else index.set(n.parent_id, [n]);
  }
  for (const list of index.values()) list.sort((a, b) => a.created_at.localeCompare(b.created_at));
  return index;
}

/** Ids on the root→`leafId` path (empty if the leaf isn't in `nodes`). */
export function pathIds(nodes: readonly TreeNode[], leafId: string | null): Set<string> {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const ids = new Set<string>();
  let cur = leafId === null ? undefined : byId.get(leafId);
  while (cur && !ids.has(cur.id)) {
    ids.add(cur.id);
    cur = cur.parent_id === null ? undefined : byId.get(cur.parent_id);
  }
  return ids;
}

export interface TreeRow {
  node: TreeNode;
  /** Indent level: 0 for the main line, +1 for each fork taken to reach this node. */
  depth: number;
  /** Number of children when this node is a fork (2+ children), else 0. */
  forkSize: number;
}

/**
 * Flatten the tree into display rows.
 *
 * A plain chat is a long single-child chain; nesting each turn one level deeper
 * would push it off-screen. So a chain stays at one indent level, and we only
 * indent where the conversation actually forks: each child of a fork starts a
 * new chain one level deeper. Collapsing a fork hides everything below it.
 * Iterative (explicit stack) so very deep chats can't overflow the call stack.
 */
export function flattenTree(
  nodes: readonly TreeNode[],
  collapsed: ReadonlySet<string>,
  hideRole: TreeNode["role"] | null = "system",
): TreeRow[] {
  const kids = childrenIndex(nodes);
  const rows: TreeRow[] = [];
  // Hidden roots (the system prompt) are skipped; their children become top-level.
  const starts: TreeNode[] = [];
  for (const root of kids.get(null) ?? []) {
    if (root.role === hideRole) starts.push(...(kids.get(root.id) ?? []));
    else starts.push(root);
  }
  const multipleStarts = starts.length > 1;
  const stack: { node: TreeNode; depth: number }[] = starts
    .map((node) => ({ node, depth: multipleStarts ? 1 : 0 }))
    .reverse();

  while (stack.length > 0) {
    const item = stack.pop();
    if (!item) break;
    let cur: TreeNode | undefined = item.node;
    while (cur) {
      const children: TreeNode[] = kids.get(cur.id) ?? [];
      const forkSize = children.length > 1 ? children.length : 0;
      rows.push({ node: cur, depth: item.depth, forkSize });
      if (forkSize > 0) {
        if (!collapsed.has(cur.id)) {
          for (let i = children.length - 1; i >= 0; i--) {
            const child = children[i];
            if (child) stack.push({ node: child, depth: item.depth + 1 });
          }
        }
        cur = undefined;
      } else {
        cur = children[0];
      }
    }
  }
  return rows;
}

/** One run of the active path that stays on a single branch (see `branchSegments`). */
export interface BranchSegment {
  /** First node of the segment: the root for the main thread, else the side branch's first node. */
  start: TreeNode;
  /** The last node of this segment on the active path. */
  end: TreeNode;
}

/**
 * Split the root→`leafId` path into branches, main thread first.
 *
 * A "branch" (SPEC) is any child of a message that already had a child, so
 * the *oldest* child continues the current branch and every later sibling
 * starts a side branch. Walking the path, a new segment begins at each node
 * that isn't its parent's oldest child. One segment = you're on the main thread.
 * Returns [] if the leaf isn't in `nodes` (e.g. the tree is still refetching).
 */
export function branchSegments(nodes: readonly TreeNode[], leafId: string | null): BranchSegment[] {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const path: TreeNode[] = [];
  const seen = new Set<string>();
  let cur = leafId === null ? undefined : byId.get(leafId);
  while (cur && !seen.has(cur.id)) {
    seen.add(cur.id);
    path.push(cur);
    cur = cur.parent_id === null ? undefined : byId.get(cur.parent_id);
  }
  path.reverse();

  const kids = childrenIndex(nodes);
  const segments: BranchSegment[] = [];
  for (const node of path) {
    const last = segments.at(-1);
    const oldestSibling = kids.get(node.parent_id)?.[0];
    if (last && oldestSibling?.id === node.id) last.end = node;
    else segments.push({ start: node, end: node });
  }
  return segments;
}

/**
 * The deepest node reached from `fromId` by always taking the oldest child,
 * i.e. the current tip of the branch `fromId` is on. Iterative for deep chats.
 */
export function branchTip(nodes: readonly TreeNode[], fromId: string): string {
  const kids = childrenIndex(nodes);
  let id = fromId;
  const seen = new Set<string>();
  for (let next = kids.get(id)?.[0]; next && !seen.has(next.id); next = kids.get(id)?.[0]) {
    seen.add(next.id);
    id = next.id;
  }
  return id;
}
