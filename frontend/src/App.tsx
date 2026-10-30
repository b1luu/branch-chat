import { useEffect, useState } from "react";
import { createConversation, getTree, listConversations } from "./api/client.ts";
import type { Conversation, Message, TreeNode } from "./api/types.ts";
import BranchNav from "./components/BranchNav.tsx";
import ChatView from "./components/ChatView.tsx";
import ErrorNotice from "./components/ErrorNotice.tsx";
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
  // Bumped by "Try again" to re-run the conversation list fetch.
  const [listAttempt, setListAttempt] = useState(0);
  // Which action is in flight, so its button can show progress and not double-fire.
  const [busy, setBusy] = useState<{ kind: "creating" } | { kind: "opening"; id: string } | null>(null);
  // Latest `/tree` the sidebar fetched, tagged with its conversation so a stale one is never used.
  const [tree, setTree] = useState<{ conversationId: string; nodes: TreeNode[] } | null>(null);
  /**
   * Counts replies persisted per conversation. A reply that finishes after the
   * user switched branch or conversation never becomes the active leaf, so the
   * sidebar wouldn't notice it; bumping this makes it refetch the tree. Keyed by
   * conversation so a background reply in another conversation doesn't refetch
   * the open one. (The callback may be a stale closure from when the send started,
   * so it only uses the functional state update.)
   */
  const [replyCounts, setReplyCounts] = useState<Readonly<Record<string, number>>>({});
  const onReplyPersisted = (m: Message) =>
    setReplyCounts((c) => ({ ...c, [m.conversation_id]: (c[m.conversation_id] ?? 0) + 1 }));

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
  }, [listAttempt]);

  async function openConversation(c: Conversation) {
    if (busy) return;
    setActionError(null);
    setBusy({ kind: "opening", id: c.id });
    try {
      const leaf = await latestNodeId(c);
      setActiveConversation(c.id);
      setActiveLeaf(leaf);
    } catch (err) {
      setActionError(`Couldn't open "${c.title}": ${errorText(err)}`);
    } finally {
      setBusy(null);
    }
  }

  async function newConversation() {
    if (busy) return;
    setActionError(null);
    setBusy({ kind: "creating" });
    try {
      const c = await createConversation();
      setState((s) => (s.kind === "ready" ? { kind: "ready", conversations: [c, ...s.conversations] } : s));
      setActiveConversation(c.id);
      setActiveLeaf(c.root_message_id);
    } catch (err) {
      setActionError(`Couldn't create a conversation: ${errorText(err)}`);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="app">
      <header>
        <h1>Branch Chat</h1>
      </header>
      <aside>
        <button type="button" onClick={() => void newConversation()} disabled={busy !== null}>
          {busy?.kind === "creating" ? "Creating…" : "New conversation"}
        </button>
        {state.kind === "loading" && <p role="status">Loading conversations…</p>}
        {state.kind === "error" && (
          <ErrorNotice
            title="Couldn't load conversations"
            message={state.message}
            onRetry={() => {
              setState({ kind: "loading" });
              setListAttempt((n) => n + 1);
            }}
          />
        )}
        {state.kind === "ready" &&
          (state.conversations.length === 0 ? (
            <div className="empty">
              <p>No conversations yet.</p>
              <p>Start one with “New conversation”.</p>
            </div>
          ) : (
            <ul aria-label="Conversations">
              {state.conversations.map((c) => (
                <li key={c.id}>
                  <button
                    type="button"
                    aria-current={c.id === activeConversation ? "true" : undefined}
                    aria-busy={busy?.kind === "opening" && busy.id === c.id ? true : undefined}
                    disabled={busy !== null}
                    onClick={() => void openConversation(c)}
                  >
                    {c.title}
                  </button>
                </li>
              ))}
            </ul>
          ))}
        {busy?.kind === "opening" && <p role="status">Opening conversation…</p>}
        {actionError && (
          <div role="alert" className="error-notice">
            <p>{actionError}</p>
            <button type="button" onClick={() => setActionError(null)}>
              Dismiss
            </button>
          </div>
        )}
        {activeConversation && (
          <nav aria-label="Branches">
            <TreeSidebar
              conversationId={activeConversation}
              activeLeaf={activeLeaf}
              onSelect={setActiveLeaf}
              refreshKey={replyCounts[activeConversation] ?? 0}
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
          <ChatView leafId={activeLeaf} onLeafChange={setActiveLeaf} onReplyPersisted={onReplyPersisted} />
        ) : (
          <p>Pick a conversation or start a new one.</p>
        )}
      </main>
    </div>
  );
}
