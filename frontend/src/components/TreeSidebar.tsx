import { useEffect, useMemo, useState } from "react";
import { getTree } from "../api/client.ts";
import type { TreeNode } from "../api/types.ts";
import { flattenTree, pathIds } from "../tree.ts";
import ErrorNotice from "./ErrorNotice.tsx";

interface TreeSidebarProps {
  conversationId: string;
  activeLeaf: string | null;
  onSelect: (id: string) => void;
  /** Called with the nodes each time the tree is (re)fetched, so siblings like the breadcrumb can share it. */
  onTreeChange?: (nodes: TreeNode[]) => void;
  /**
   * Bump to force a refetch even though the active leaf is already known, e.g.
   * when a reply detached from the chat view (the user navigated away mid-stream)
   * is persisted in the background and adds a node the user isn't looking at.
   */
  refreshKey?: number;
}

const ROLE_LABEL: Record<TreeNode["role"], string> = { user: "You", assistant: "AI", system: "System" };

/**
 * Collapsible minimap of the conversation tree.
 *
 * Fetches `/tree` when the conversation changes, and again whenever the active
 * leaf is a node it hasn't seen (i.e. a reply just created it) or `refreshKey`
 * changes (a background reply landed). Nodes on the
 * active path are marked so the user can see which branch they're on.
 */
export default function TreeSidebar({
  conversationId,
  activeLeaf,
  onSelect,
  onTreeChange,
  refreshKey = 0,
}: TreeSidebarProps) {
  // Tagged with the conversation and refreshKey it was fetched for, so a stale one triggers a refetch.
  const [tree, setTree] = useState<{ conversationId: string; refreshKey: number; nodes: TreeNode[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());
  // Bumped by "Try again" to re-run a failed fetch.
  const [attempt, setAttempt] = useState(0);

  const current = tree?.conversationId === conversationId ? tree : null;
  const needsFetch =
    current === null ||
    current.refreshKey !== refreshKey ||
    (activeLeaf !== null && !current.nodes.some((n) => n.id === activeLeaf));

  useEffect(() => {
    if (!needsFetch) return;
    let cancelled = false;
    getTree(conversationId)
      .then((t) => {
        if (cancelled) return;
        setError(null);
        setTree({ conversationId, refreshKey, nodes: t.nodes });
        onTreeChange?.(t.nodes);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
    // onTreeChange is a notification sink; a new callback identity shouldn't refetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId, activeLeaf, needsFetch, attempt, refreshKey]);

  const nodes = current?.nodes;
  const rows = useMemo(() => (nodes ? flattenTree(nodes, collapsed) : []), [nodes, collapsed]);
  const onPath = useMemo(() => (nodes ? pathIds(nodes, activeLeaf) : new Set<string>()), [nodes, activeLeaf]);

  function toggle(id: string) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const retry = () => {
    setError(null);
    setAttempt((n) => n + 1);
  };
  if (error && !current) return <ErrorNotice title="Couldn't load the tree" message={error} onRetry={retry} />;
  if (!current) return <p role="status">Loading tree…</p>;
  if (rows.length === 0) return <p className="tree-empty">No messages yet.</p>;

  return (
    <>
      {/* A refresh failed after a reply: keep showing the last tree, offer a retry. */}
      {error && <ErrorNotice title="Couldn't refresh the tree" message={error} onRetry={retry} />}
      <ul role="tree" aria-label="Conversation tree" className="tree">
        {rows.map(({ node, depth, forkSize }) => {
          const isFork = forkSize > 0;
          const isCollapsed = collapsed.has(node.id);
          const active = node.id === activeLeaf;
          return (
            <li
              key={node.id}
              role="treeitem"
              aria-level={depth + 1}
              aria-selected={active}
              aria-expanded={isFork ? !isCollapsed : undefined}
              data-on-path={onPath.has(node.id) || undefined}
              className={`tree-node tree-${node.role}${onPath.has(node.id) ? " on-path" : ""}${active ? " active" : ""}`}
              style={{ paddingLeft: `${depth}rem` }}
            >
              {isFork && (
                <button
                  type="button"
                  className="tree-toggle"
                  aria-label={`${isCollapsed ? "Expand" : "Collapse"} ${forkSize} branches`}
                  onClick={() => toggle(node.id)}
                >
                  {isCollapsed ? "▸" : "▾"}
                </button>
              )}
              <button
                type="button"
                className="tree-label"
                aria-current={active ? "true" : undefined}
                onClick={() => onSelect(node.id)}
              >
                <span className="tree-role">{ROLE_LABEL[node.role]}:</span> {node.preview || "(empty)"}
              </button>
            </li>
          );
        })}
      </ul>
    </>
  );
}
