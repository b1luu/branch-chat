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
 * The node a range boundary points at. For a text node that's the node itself;
 * for an element it's the child just inside the range (the one after a start
 * boundary, the one before an end boundary), e.g. `(li, 0)` → the li's first child.
 */
function boundaryNode(container: Node, offset: number, side: "start" | "end"): Node {
  if (container.nodeType === Node.TEXT_NODE) return container;
  const child = container.childNodes[side === "start" ? offset : offset - 1];
  return child ?? container;
}

/** The part of `range` that lies inside `el`. */
function clampTo(range: Range, el: HTMLElement): Range {
  const inner = document.createRange();
  inner.selectNodeContents(el);
  if (range.compareBoundaryPoints(Range.START_TO_START, inner) > 0) {
    inner.setStart(range.startContainer, range.startOffset);
  }
  if (range.compareBoundaryPoints(Range.END_TO_END, inner) < 0) {
    inner.setEnd(range.endContainer, range.endOffset);
  }
  return inner;
}

/**
 * Where the user's highlight is, if it is a usable quote.
 *
 * Accepts the selection only when all of its visible text sits inside the
 * *same* message's text (`.content` of an `li[data-message-id]`) and that
 * message has `role`. A drag that picks up text from a neighbouring message,
 * the quote block, or the composer returns null, so "Ask about this" always
 * refers to one message. Whitespace-only selections also return null; the
 * text is trimmed.
 *
 * Browsers often end a selection *just past* the text the user sees
 * highlighted: a triple-click on a paragraph, or a drag released below the
 * last line, ends at offset 0 of the next element (the next message, the
 * composer). So instead of requiring both ends to be inside the message, we
 * take the message that either end is in and accept it when the part of the
 * range outside it is whitespace only.
 */
export function selectionInMessage(selection: Selection | null, role: Role): MessageSelection | null {
  if (!selection || selection.isCollapsed || selection.rangeCount === 0) return null;
  const range = selection.getRangeAt(0);
  const text = selection.toString().trim();
  if (!text) return null;
  const candidates = [
    contentElement(boundaryNode(range.startContainer, range.startOffset, "start")),
    contentElement(boundaryNode(range.endContainer, range.endOffset, "end")),
  ];
  for (const content of candidates) {
    if (!content) continue;
    const inside = clampTo(range, content).toString().trim();
    // range text = before + inside + after; equal after trimming ⇔ before/after are whitespace.
    if (!inside || inside !== range.toString().trim()) continue;
    const bubble = content.closest<HTMLElement>("[data-message-id]");
    const messageId = bubble?.dataset.messageId;
    if (!messageId || bubble.dataset.role !== role) return null;
    return { messageId, text };
  }
  return null;
}
