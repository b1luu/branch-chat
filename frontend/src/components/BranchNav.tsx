import { useMemo } from "react";
import type { TreeNode } from "../api/types.ts";
import { branchSegments, branchTip } from "../tree.ts";

interface BranchNavProps {
  nodes: readonly TreeNode[];
  activeLeaf: string;
  onSelect: (id: string) => void;
}

const LABEL_CHARS = 32;

function shorten(text: string): string {
  const flat = text.replace(/\s+/g, " ").trim() || "(empty)";
  return flat.length > LABEL_CHARS ? `${flat.slice(0, LABEL_CHARS - 1)}…` : flat;
}

/**
 * Breadcrumb of the branches on the active path, plus a back button.
 *
 * Hidden on the main thread. On a side branch, each earlier crumb jumps to the
 * *tip* of that branch (where you'd be if you'd never branched off), and the
 * back button does the same for the branch you came from: "Back to main thread"
 * one level deep, "Back to parent branch" deeper. Tips come from `branchTip`, so
 * if the parent branch grew while you were away you land on its newest turn.
 */
export default function BranchNav({ nodes, activeLeaf, onSelect }: BranchNavProps) {
  const segments = useMemo(() => branchSegments(nodes, activeLeaf), [nodes, activeLeaf]);
  if (segments.length < 2) return null;

  // Crumb i (all but the last) targets the tip of branch i, continuing past the
  // node where branch i+1 forked off of it.
  const targets = segments.slice(0, -1).map((_, i) => {
    const forkOrigin = segments[i + 1]?.start.parent_id ?? segments[i]?.end.id ?? activeLeaf;
    return branchTip(nodes, forkOrigin);
  });
  const backTarget = targets.at(-1) ?? activeLeaf;

  return (
    <div className="branch-nav">
      <nav aria-label="Breadcrumb">
        <ol>
          {segments.map((seg, i) => {
            const label = i === 0 ? "Main thread" : shorten(seg.start.preview);
            const target = targets[i];
            return (
              <li key={seg.start.id}>
                {target === undefined ? (
                  <span aria-current="location">{label}</span>
                ) : (
                  <button type="button" onClick={() => onSelect(target)}>
                    {label}
                  </button>
                )}
              </li>
            );
          })}
        </ol>
      </nav>
      <button type="button" className="back" onClick={() => onSelect(backTarget)}>
        {segments.length === 2 ? "Back to main thread" : "Back to parent branch"}
      </button>
    </div>
  );
}
