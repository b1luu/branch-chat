import type { Role } from "./api/types.ts";

export interface MessageSelection {
  messageId: string;
  text: string;
}

/** The `.content` element of the message bubble containing `node`, if any. */
function contentElement(node: Node | null): HTMLElement | null {
  const el = node instanceof HTMLElement ? node : (node?.parentElement ?? null);
  return el?.closest<HTMLElement>("[data-message-id] .content") ?? null;
}

/**
 * Where the user's highlight is, if it is a usable quote.
 *
 * Accepts the selection only when both ends sit inside the *same* message's
 * text (`.content` of an `li[data-message-id]`) and that message has `role`.
 * A drag that spills into a neighbouring message, the quote block, or the
 * composer returns null, so "Ask about this" always refers to one message.
 * Whitespace-only selections also return null; the text is trimmed.
 */
export function selectionInMessage(selection: Selection | null, role: Role): MessageSelection | null {
  if (!selection || selection.isCollapsed || selection.rangeCount === 0) return null;
  const start = contentElement(selection.anchorNode);
  if (!start || start !== contentElement(selection.focusNode)) return null;
  const bubble = start.closest<HTMLElement>("[data-message-id]");
  const messageId = bubble?.dataset.messageId;
  if (!messageId || bubble.dataset.role !== role) return null;
  const text = selection.toString().trim();
  return text ? { messageId, text } : null;
}
