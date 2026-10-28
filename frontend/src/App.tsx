import { useEffect, useState } from "react";
import { createConversation, getTree, listConversations } from "./api/client.ts";
import type { Conversation, TreeNode } from "./api/types.ts";
import BranchNav from "./components/BranchNav.tsx";
import ChatView from "./components/ChatView.tsx";
import TreeSidebar from "./components/TreeSidebar.tsx";

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; conversations: Conversation[] };

const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err));

/**
 * Pick where to resume a conversation: its most recently created node.
 * The tree sidebar lets the user choose any other node.
 */
async function latestNodeId(conversation: Conversation): Promise<string | null> {
  const tree = await getTree(conversation.id);
  let latest = tree.nodes[0];
  for (const n of tree.nodes) if (latest === undefined || n.created_at >= latest.created_at) latest = n;
  return latest?.id ?? conversation.root_message_id;
}

export default function App() {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [activeConversation, setActiveConversation] = useState<string | null>(null);
  const [activeLeaf, setActiveLeaf] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  // Latest `/tree` the sidebar fetched, tagged with its conversation so a stale one is never used.
  const [tree, setTree] = useState<{ conversationId: string; nodes: TreeNode[] } | null>(null);

  useEffect(() => {
    let cancelled = false;
    listConversations()
      .then((conversations) => {
        if (!cancelled) setState({ kind: "ready", conversations });
      })
      .catch((err: unknown) => {
        if (!cancelled) setState({ kind: "error", message: errorText(err) });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function openConversation(c: Conversation) {
    setActionError(null);
    try {
      const leaf = await latestNodeId(c);
      setActiveConversation(c.id);
      setActiveLeaf(leaf);
    } catch (err) {
      setActionError(errorText(err));
    }
  }

  async function newConversation() {
    setActionError(null);
    try {
      const c = await createConversation();
      setState((s) => (s.kind === "ready" ? { kind: "ready", conversations: [c, ...s.conversations] } : s));
      setActiveConversation(c.id);
      setActiveLeaf(c.root_message_id);
    } catch (err) {
      setActionError(errorText(err));
    }
  }

  return (
    <div className="app">
      <header>
        <h1>Branch Chat</h1>
      </header>
      <aside>
        <button type="button" onClick={() => void newConversation()}>
          New conversation
        </button>
        {state.kind === "loading" && <p role="status">Loading conversations…</p>}
        {state.kind === "error" && <p role="alert">Couldn't load conversations: {state.message}</p>}
        {state.kind === "ready" &&
          (state.conversations.length === 0 ? (
            <p>No conversations yet.</p>
          ) : (
            <ul aria-label="Conversations">
              {state.conversations.map((c) => (
                <li key={c.id}>
                  <button
                    type="button"
                    aria-current={c.id === activeConversation ? "true" : undefined}
                    onClick={() => void openConversation(c)}
                  >
                    {c.title}
                  </button>
                </li>
              ))}
            </ul>
          ))}
        {actionError && <p role="alert">{actionError}</p>}
        {activeConversation && (
          <nav aria-label="Branches">
            <TreeSidebar
              conversationId={activeConversation}
              activeLeaf={activeLeaf}
              onSelect={setActiveLeaf}
              onTreeChange={(nodes) => setTree({ conversationId: activeConversation, nodes })}
            />
          </nav>
        )}
      </aside>
      <main>
        {activeLeaf && tree && tree.conversationId === activeConversation && (
          <BranchNav key={activeLeaf} nodes={tree.nodes} activeLeaf={activeLeaf} onSelect={setActiveLeaf} />
        )}
        {activeLeaf ? (
          <ChatView leafId={activeLeaf} onLeafChange={setActiveLeaf} />
        ) : (
          <p>Pick a conversation or start a new one.</p>
        )}
      </main>
    </div>
  );
}
