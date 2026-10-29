import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { selectionInMessage } from "../selection.ts";
import { getPath, streamReply } from "../api/client.ts";
import type { Message, Note } from "../api/types.ts";
import ErrorNotice from "./ErrorNotice.tsx";

interface ChatViewProps {
  /** The active leaf: the chat shows the root→leaf path and replies go under it. */
  leafId: string;
  /** Called with the new assistant message id once a reply finishes. */
  onLeafChange: (id: string) => void;
}

/** A user turn sent but not yet confirmed by the server's `user` event. */
interface PendingTurn {
  /** The message the reply hangs under: the leaf, or an earlier assistant turn for a quote branch. */
  parentId: string;
  user: Pick<Message, "id" | "role" | "content" | "quote">;
  assistantText: string;
}

/** Highlighted text in an assistant message: the anchor for a quote branch. */
export interface QuoteTarget {
  messageId: string;
  text: string;
}

/** Same cap as the backend's `ReplyCreate.quote` (schemas.py). */
export const MAX_QUOTE_CHARS = 20_000;
/** Same cap as the backend's `ReplyCreate.content` (schemas.py); checked here so the user never gets a 422. */
export const MAX_CONTENT_CHARS = 50_000;
/** Show the character counter once a draft gets this close to the cap. */
const COUNTER_FROM = Math.floor(MAX_CONTENT_CHARS * 0.9);

/** Messages up to and including `id` (the whole path if `id` isn't on it). */
function pathThrough(path: Message[], id: string): Message[] {
  const i = path.findIndex((m) => m.id === id);
  return i === -1 ? path : path.slice(0, i + 1);
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
  // Current highlight in an assistant message (shows "Ask about this").
  const [selection, setSelection] = useState<QuoteTarget | null>(null);
  // Highlight the user chose to ask about; the next send branches from it.
  const [quote, setQuote] = useState<QuoteTarget | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  // Leaf ids we produced locally, so the effect below can skip a redundant fetch.
  const knownLeaf = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  /**
   * The send whose results may still update this view. Navigating to a leaf we
   * didn't create (another branch or conversation) detaches it: the request
   * keeps running so the server still persists the reply, but its tokens,
   * result and errors no longer touch this view (they belong to another path).
   */
  const activeSend = useRef<symbol | null>(null);
  // Bumped by "Try again" to re-run a failed path fetch.
  const [loadAttempt, setLoadAttempt] = useState(0);

  useEffect(() => {
    if (knownLeaf.current === leafId) return;
    let cancelled = false;
    activeSend.current = null; // detach any in-flight reply (see `activeSend`)
    setPending(null);
    setSendError(null);
    setPath(null);
    setLoadError(null);
    setSelection(null);
    setQuote(null);
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
  }, [leafId, loadAttempt]);

  // Abort an in-flight stream if the view unmounts.
  useEffect(() => () => abortRef.current?.abort(), []);

  const streaming = pending !== null;
  const tooLong = draft.length > MAX_CONTENT_CHARS;

  async function send() {
    const content = draft.trim();
    if (!content || tooLong || streaming || path === null) return;
    const controller = new AbortController();
    abortRef.current = controller;
    const sendId = Symbol("send");
    activeSend.current = sendId;
    const attached = () => activeSend.current === sendId;
    // A quote branch hangs under the quoted message, not the current leaf.
    const parentId = quote?.messageId ?? leafId;
    const quoteText = quote?.text ?? null;
    setDraft("");
    setSendError(null);
    setSelection(null);
    setPending({ parentId, user: { id: "pending", role: "user", content, quote: quoteText }, assistantText: "" });

    let userNode: Message | null = null;
    try {
      const assistant = await streamReply(
        parentId,
        quoteText === null ? { content } : { content, quote: quoteText },
        {
          onUser: (m) => {
            userNode = m;
            if (attached()) setPending((p) => (p ? { ...p, user: m } : p));
          },
          onToken: (text) => {
            if (attached()) setPending((p) => (p ? { ...p, assistantText: p.assistantText + text } : p));
          },
        },
        controller.signal,
      );
      if (!attached()) return;
      const confirmedUser: Message | null = userNode;
      // Branching from an earlier message drops the turns below it from view.
      setPath((p) => [...pathThrough(p ?? [], parentId), ...(confirmedUser ? [confirmedUser] : []), assistant]);
      setQuote(null);
      knownLeaf.current = assistant.id;
      onLeafChange(assistant.id);
    } catch (err) {
      if (controller.signal.aborted || !attached()) return;
      setSendError(errorText(err));
      setDraft((d) => d || content); // let the user retry without retyping (the quote is kept too)
    } finally {
      if (!controller.signal.aborted && attached()) {
        activeSend.current = null;
        setPending(null);
      }
    }
  }

  /**
   * After a mouse/keyboard selection settles, offer "Ask about this" if the
   * highlight sits inside a single assistant message (see `selectionInMessage`).
   */
  function onSelectionEnd() {
    if (streaming) return;
    const found = selectionInMessage(window.getSelection(), "assistant");
    setSelection(found && { messageId: found.messageId, text: found.text.slice(0, MAX_QUOTE_CHARS) });
  }

  function askAboutSelection() {
    if (!selection) return;
    setQuote(selection);
    setSelection(null);
    window.getSelection()?.removeAllRanges();
    inputRef.current?.focus();
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

  if (loadError)
    return (
      <ErrorNotice
        title="Couldn't load this conversation"
        message={loadError}
        onRetry={() => setLoadAttempt((n) => n + 1)}
      />
    );
  if (path === null) return <p role="status">Loading messages…</p>;

  // The root system prompt is context, not chat; hide it. While a quote
  // branch streams, show only the path down to the quoted message.
  const shown = pending ? pathThrough(path, pending.parentId) : path;
  const visible = shown.filter((m) => m.role !== "system");

  return (
    <section className="chat" aria-label="Chat">
      <ol className="messages" aria-label="Messages" onMouseUp={onSelectionEnd} onKeyUp={onSelectionEnd}>
        {visible.map((m) => (
          <MessageBubble
            key={m.id}
            id={m.id}
            role={m.role}
            content={m.content}
            quote={m.quote}
            notes={m.notes}
            onAsk={selection?.messageId === m.id ? askAboutSelection : undefined}
          />
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
        {quote && (
          <div className="quote-draft" aria-label="Asking about">
            <blockquote className="quote">{quote.text}</blockquote>
            <button type="button" onClick={() => setQuote(null)} disabled={streaming}>
              Cancel quote
            </button>
          </div>
        )}
        <textarea
          ref={inputRef}
          aria-label="Message"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={quote ? "Ask about the quoted text…" : "Ask anything…"}
          rows={3}
          aria-invalid={tooLong || undefined}
          aria-describedby={draft.length >= COUNTER_FROM ? "composer-count" : undefined}
        />
        {draft.length >= COUNTER_FROM && (
          <p id="composer-count" className={`char-count${tooLong ? " over" : ""}`} aria-live="polite">
            {tooLong
              ? `Message is too long: ${draft.length.toLocaleString("en-US")} / ${MAX_CONTENT_CHARS.toLocaleString("en-US")} characters.`
              : `${draft.length.toLocaleString("en-US")} / ${MAX_CONTENT_CHARS.toLocaleString("en-US")} characters`}
          </p>
        )}
        <button type="submit" disabled={streaming || tooLong || draft.trim() === ""}>
          {streaming ? "Sending…" : "Send"}
        </button>
      </form>
    </section>
  );
}

interface BubbleProps {
  id?: string;
  role: Message["role"];
  content: string;
  quote?: string | null;
  notes?: Note[] | undefined;
  streaming?: boolean;
  /** Set when text in this message is highlighted: shows the "Ask about this" button. */
  onAsk?: () => void;
}

function MessageBubble({ id, role, content, quote, notes, streaming = false, onAsk }: BubbleProps) {
  return (
    <li
      className={`message message-${role}`}
      data-role={role}
      data-message-id={id}
      aria-busy={streaming || undefined}
    >
      {quote && <blockquote className="quote">{quote}</blockquote>}
      <div className="content">{content || (streaming ? "…" : "")}</div>
      {onAsk && (
        // preventDefault on mousedown keeps the highlight while the click lands.
        <button type="button" className="ask" onMouseDown={(e) => e.preventDefault()} onClick={onAsk}>
          Ask about this
        </button>
      )}
      {notes?.map((n) => (
        // Outside `.content`, so highlighting a note never offers "Ask about this".
        <aside key={n.id} className="note" aria-label="Merged note">
          <span className="note-label">Merged from a side branch</span>
          <p>{n.content}</p>
        </aside>
      ))}
    </li>
  );
}
