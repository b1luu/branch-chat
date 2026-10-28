import { useEffect, useState } from "react";
import { listConversations } from "./api/client.ts";
import type { Conversation } from "./api/types.ts";

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; conversations: Conversation[] };

export default function App() {
  const [state, setState] = useState<LoadState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    listConversations()
      .then((conversations) => {
        if (!cancelled) setState({ kind: "ready", conversations });
      })
      .catch((err: unknown) => {
        if (!cancelled) setState({ kind: "error", message: err instanceof Error ? err.message : String(err) });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="app">
      <header>
        <h1>Branch Chat</h1>
      </header>
      <main>
        {state.kind === "loading" && <p role="status">Loading conversations…</p>}
        {state.kind === "error" && <p role="alert">Couldn't load conversations: {state.message}</p>}
        {state.kind === "ready" &&
          (state.conversations.length === 0 ? (
            <p>No conversations yet.</p>
          ) : (
            <ul aria-label="Conversations">
              {state.conversations.map((c) => (
                <li key={c.id}>{c.title}</li>
              ))}
            </ul>
          ))}
      </main>
    </div>
  );
}
