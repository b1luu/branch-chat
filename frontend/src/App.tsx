import { useEffect, useState } from "react";
import { createConversation, getTree, listConversations } from "./api/client.ts";
import type { Conversation } from "./api/types.ts";
import ChatView from "./components/ChatView.tsx";

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; conversations: Conversation[] };

const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err));

/**
 * Pick where to resume a conversation: its most recently created node.
 * (F3's tree sidebar lets the user choose any other node.)
 */
async function latestNodeId(conversation: Conversation): Promise<string | null> {
  const tree = await getTree(conversation.id);
  let latest = tree.nodes[0];
  for (const n of tree.nodes) if (latest === undefined || n.created_at >= latest.created_at) latest = n;
  return latest?.id ?? conversation.root_message_id;
}

export default function App() {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [activeLeaf, setActiveLeaf] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

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
      setActiveLeaf(await latestNodeId(c));
    } catch (err) {
      setActionError(errorText(err));
    }
  }

  async function newConversation() {
    setActionError(null);
    try {
      const c = await createConversation();
      setState((s) => (s.kind === "ready" ? { kind: "ready", conversations: [c, ...s.conversations] } : s));
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
                  <button type="button" onClick={() => void openConversation(c)}>
                    {c.title}
                  </button>
                </li>
              ))}
            </ul>
          ))}
        {actionError && <p role="alert">{actionError}</p>}
      </aside>
      <main>
        {activeLeaf ? (
          <ChatView leafId={activeLeaf} onLeafChange={setActiveLeaf} />
        ) : (
          <p>Pick a conversation or start a new one.</p>
        )}
      </main>
    </div>
  );
}
