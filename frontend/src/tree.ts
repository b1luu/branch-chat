/**
 * Pure helpers for turning the flat `/tree` node list into what the sidebar draws.
 * The backend sends nodes parents-first with `parent_id` links (adjacency list);
 * the client assembles the tree.
 */
import type { TreeNode } from "./api/types.ts";

const INSTANT = /^(.*?)(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?$/i;

/**
 * Split an ISO timestamp into whole-second epoch ms and a fixed-width fraction.
 *
 * Why not compare the strings: the API omits the fraction when it is exactly
 * zero (`…:42Z`) and sends 6 digits otherwise (`…:42.500000Z`), and `"."` sorts
 * before `"Z"`, so `…:42.5Z` would come *before* `…:42Z`. `Date.parse` alone
 * keeps only milliseconds, while the server orders by microseconds.
 */
function instantKey(iso: string): [number, string] {
  const m = INSTANT.exec(iso);
  const base = m?.[1] ?? iso;
  const fraction = (m?.[2] ?? "").padEnd(9, "0").slice(0, 9);
  const seconds = Date.parse(`${base}${m?.[3] ?? ""}`);
  return [Number.isNaN(seconds) ? 0 : seconds, fraction];
}

/**
 * Oldest-first order matching the server's `ORDER BY created_at, id`, which is
 * what decides the "oldest child" (main branch) on the backend, e.g. for merge.
 */
export function compareCreated(a: TreeNode, b: TreeNode): number {
  const byTime = compareInstant(a.created_at, b.created_at);
  if (byTime !== 0) return byTime;
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}

/** Order two API timestamps by the instant they name (see `instantKey` for why not as strings). */
export function compareInstant(a: string, b: string): number {
  const [sa, fa] = instantKey(a);
  const [sb, fb] = instantKey(b);
  if (sa !== sb) return sa - sb;
  return fa < fb ? -1 : fa > fb ? 1 : 0;
}

/**
 * The most recently created node, or `undefined` for an empty list.
 *
 * Ties (same instant, e.g. Windows' ~15 ms clock) go to the node listed last,
 * not to the larger id: `/tree` lists parents before children, so a reply that
 * shares its parent's timestamp still wins and we never resume on a non-leaf.
 */
export function latestNode<T extends Pick<TreeNode, "created_at">>(nodes: readonly T[]): T | undefined {
  let latest: T | undefined;
  for (const n of nodes) if (latest === undefined || compareInstant(n.created_at, latest.created_at) >= 0) latest = n;
  return latest;
}

/** Children of each node id (`null` = roots), oldest first. */
export function childrenIndex(nodes: readonly TreeNode[]): Map<string | null, TreeNode[]> {
  const index = new Map<string | null, TreeNode[]>();
  for (const n of nodes) {
    const list = index.get(n.parent_id);
    if (list) list.push(n);
    else index.set(n.parent_id, [n]);
  }
  for (const list of index.values()) list.sort(compareCreated);
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
  /**
   * Off the main thread: this row's chain (or one it hangs under) started at a
   * non-oldest child of a fork. Matches `branchSegments`, where the oldest child
   * continues the parent branch and every later one starts a side branch.
   */
  side: boolean;
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
  const stack: { node: TreeNode; depth: number; side: boolean }[] = starts
    .map((node, i) => ({ node, depth: multipleStarts ? 1 : 0, side: i > 0 }))
    .reverse();

  while (stack.length > 0) {
    const item = stack.pop();
    if (!item) break;
    let cur: TreeNode | undefined = item.node;
    while (cur) {
      const children: TreeNode[] = kids.get(cur.id) ?? [];
      const forkSize = children.length > 1 ? children.length : 0;
      rows.push({ node: cur, depth: item.depth, forkSize, side: item.side });
      if (forkSize > 0) {
        if (!collapsed.has(cur.id)) {
          for (let i = children.length - 1; i >= 0; i--) {
            const child = children[i];
            if (child) stack.push({ node: child, depth: item.depth + 1, side: item.side || i > 0 });
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
