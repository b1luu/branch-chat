import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { getPath, streamReply } from "../api/client.ts";
import type { Message } from "../api/types.ts";

interface ChatViewProps {
  /** The active leaf: the chat shows the root→leaf path and replies go under it. */
  leafId: string;
  /** Called with the new assistant message id once a reply finishes. */
  onLeafChange: (id: string) => void;
}

/** A user turn sent but not yet confirmed by the server's `user` event. */
interface PendingTurn {
  user: Pick<Message, "id" | "role" | "content" | "quote">;
  assistantText: string;
}

const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err));

/**
 * Main pane: renders the active path as a normal chat and streams new replies.
 *
 * While a reply streams we show it from local state (`pending`); when the
 * `done` event arrives we append both persisted nodes to the path and move the
 * active leaf to the new assistant message. The path is only re-fetched when
 * `leafId` changes to something we didn't just create ourselves.
 */
export default function ChatView({ leafId, onLeafChange }: ChatViewProps) {
  const [path, setPath] = useState<Message[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<PendingTurn | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);
  // Leaf ids we produced locally, so the effect below can skip a redundant fetch.
  const knownLeaf = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (knownLeaf.current === leafId) return;
    let cancelled = false;
    setPath(null);
    setLoadError(null);
    getPath(leafId)
      .then((messages) => {
        if (cancelled) return;
        knownLeaf.current = leafId;
        setPath(messages);
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoadError(errorText(err));
      });
    return () => {
      cancelled = true;
    };
  }, [leafId]);

  // Abort an in-flight stream if the view unmounts.
  useEffect(() => () => abortRef.current?.abort(), []);

  const streaming = pending !== null;

  async function send() {
    const content = draft.trim();
    if (!content || streaming || path === null) return;
    const controller = new AbortController();
    abortRef.current = controller;
    setDraft("");
    setSendError(null);
    setPending({ user: { id: "pending", role: "user", content, quote: null }, assistantText: "" });

    let userNode: Message | null = null;
    try {
      const assistant = await streamReply(
        leafId,
        { content },
        {
          onUser: (m) => {
            userNode = m;
            setPending((p) => (p ? { ...p, user: m } : p));
          },
          onToken: (text) => setPending((p) => (p ? { ...p, assistantText: p.assistantText + text } : p)),
        },
        controller.signal,
      );
      const confirmedUser: Message | null = userNode;
      setPath((p) => [...(p ?? []), ...(confirmedUser ? [confirmedUser] : []), assistant]);
      knownLeaf.current = assistant.id;
      onLeafChange(assistant.id);
    } catch (err) {
      if (controller.signal.aborted) return;
      setSendError(errorText(err));
      setDraft((d) => d || content); // let the user retry without retyping
    } finally {
      if (!controller.signal.aborted) setPending(null);
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void send();
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends, Shift+Enter inserts a newline.
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void send();
    }
  }

  if (loadError) return <p role="alert">Couldn't load this conversation: {loadError}</p>;
  if (path === null) return <p role="status">Loading messages…</p>;

  // The root system prompt is context, not chat; hide it.
  const visible = path.filter((m) => m.role !== "system");

  return (
    <section className="chat" aria-label="Chat">
      <ol className="messages" aria-label="Messages">
        {visible.map((m) => (
          <MessageBubble key={m.id} role={m.role} content={m.content} quote={m.quote} />
        ))}
        {pending && (
          <>
            <MessageBubble role="user" content={pending.user.content} quote={pending.user.quote} />
            <MessageBubble role="assistant" content={pending.assistantText} streaming />
          </>
        )}
      </ol>
      {visible.length === 0 && !pending && <p className="empty">Send a message to start.</p>}
      {sendError && <p role="alert">Reply failed: {sendError}</p>}
      <form onSubmit={onSubmit} className="composer">
        <textarea
          aria-label="Message"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Ask anything…"
          rows={3}
        />
        <button type="submit" disabled={streaming || draft.trim() === ""}>
          {streaming ? "Sending…" : "Send"}
        </button>
      </form>
    </section>
  );
}

interface BubbleProps {
  role: Message["role"];
  content: string;
  quote?: string | null;
  streaming?: boolean;
}

function MessageBubble({ role, content, quote, streaming = false }: BubbleProps) {
  return (
    <li className={`message message-${role}`} data-role={role} aria-busy={streaming || undefined}>
      {quote && <blockquote className="quote">{quote}</blockquote>}
      <div className="content">{content || (streaming ? "…" : "")}</div>
    </li>
  );
}
