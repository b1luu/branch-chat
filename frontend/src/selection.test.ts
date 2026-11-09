import { afterEach, describe, expect, it } from "vitest";
import { selectAcross, selectText } from "./test/select.ts";
import { selectionInMessage } from "./selection.ts";

/** Select from (startNode, startAt) to (endNode, endAt), as the browser would report it. */
function selectRange(startNode: Node, startAt: number, endNode: Node, endAt: number): Selection {
  const range = document.createRange();
  range.setStart(startNode, startAt);
  range.setEnd(endNode, endAt);
  const selection = window.getSelection();
  if (!selection) throw new Error("no Selection API");
  selection.removeAllRanges();
  selection.addRange(range);
  return selection;
}

const textOf = (li: HTMLLIElement): Text => {
  const node = li.querySelector(".content")?.firstChild;
  if (!(node instanceof Text)) throw new Error("no text node");
  return node;
};

function bubble(id: string, role: string, content: string, quote?: string): HTMLLIElement {
  const li = document.createElement("li");
  li.dataset.messageId = id;
  li.dataset.role = role;
  if (quote) {
    const q = document.createElement("blockquote");
    q.textContent = quote;
    li.append(q);
  }
  const div = document.createElement("div");
  div.className = "content";
  div.textContent = content;
  li.append(div);
  document.body.append(li);
  return li;
}

afterEach(() => {
  window.getSelection()?.removeAllRanges();
  document.body.innerHTML = "";
});

describe("selectionInMessage", () => {
  it("returns the message id and trimmed text for a highlight inside one message", () => {
    const a = bubble("a1", "assistant", "A recursive CTE walks parent links.");
    const sel = selectText(a, " recursive CTE ");
    expect(selectionInMessage(sel, "assistant")).toEqual({ messageId: "a1", text: "recursive CTE" });
  });

  it("ignores an empty or collapsed selection", () => {
    bubble("a1", "assistant", "Hello");
    expect(selectionInMessage(window.getSelection(), "assistant")).toBeNull();
    expect(selectionInMessage(null, "assistant")).toBeNull();
  });

  it("ignores whitespace-only highlights", () => {
    const a = bubble("a1", "assistant", "two  spaces");
    expect(selectionInMessage(selectText(a, "  "), "assistant")).toBeNull();
  });

  it("ignores highlights in messages of another role", () => {
    const u = bubble("u1", "user", "What is a CTE?");
    expect(selectionInMessage(selectText(u, "CTE"), "assistant")).toBeNull();
    expect(selectionInMessage(selectText(u, "CTE"), "user")).toEqual({ messageId: "u1", text: "CTE" });
  });

  it("ignores a highlight that spans two messages", () => {
    const a1 = bubble("a1", "assistant", "First answer.");
    const a2 = bubble("a2", "assistant", "Second answer.");
    expect(selectionInMessage(selectAcross(a1, "answer", a2, "Second"), "assistant")).toBeNull();
  });

  it("ignores a highlight in a message's quote block", () => {
    const a = bubble("a1", "assistant", "Body text", "quoted bit");
    expect(selectionInMessage(selectText(a, "quoted"), "assistant")).toBeNull();
  });

  it("ignores a highlight outside any message", () => {
    const p = document.createElement("p");
    p.textContent = "Not a message";
    document.body.append(p);
    expect(selectionInMessage(selectText(p, "message"), "assistant")).toBeNull();
  });

  // Q30: browsers end a triple-click or an overshooting drag just *past* the highlighted text.
  describe("selection that ends just past the message", () => {
    it("accepts a triple-click that ends at the start of the next message", () => {
      const a1 = bubble("a1", "assistant", "Whole paragraph here.");
      const a2 = bubble("a2", "assistant", "Next answer.");
      const sel = selectRange(textOf(a1), 0, a2, 0);
      expect(selectionInMessage(sel, "assistant")).toEqual({ messageId: "a1", text: "Whole paragraph here." });
    });

    it("accepts a drag that ends at offset 0 of the next message's text", () => {
      const a1 = bubble("a1", "assistant", "First answer.");
      const a2 = bubble("a2", "assistant", "Second answer.");
      const sel = selectRange(textOf(a1), 6, textOf(a2), 0);
      expect(selectionInMessage(sel, "assistant")).toEqual({ messageId: "a1", text: "answer." });
    });

    it("accepts a drag released below the chat, in the composer area", () => {
      const a1 = bubble("a1", "assistant", "Last answer in the chat.");
      const form = document.createElement("form");
      form.append(document.createTextNode("\n  "), document.createElement("textarea"));
      document.body.append(form);
      const sel = selectRange(textOf(a1), 5, form, 1);
      expect(selectionInMessage(sel, "assistant")).toEqual({ messageId: "a1", text: "answer in the chat." });
    });

    it("accepts a backwards drag that starts at the end of the previous message", () => {
      const u1 = bubble("u1", "user", "Question?");
      const a1 = bubble("a1", "assistant", "The answer.");
      const sel = selectRange(textOf(u1), "Question?".length, textOf(a1), 3);
      expect(selectionInMessage(sel, "assistant")).toEqual({ messageId: "a1", text: "The" });
    });

    it("accepts the whole bubble selected at the element level", () => {
      const a1 = bubble("a1", "assistant", "Entire reply.");
      const sel = selectRange(a1, 0, a1, a1.childNodes.length);
      expect(selectionInMessage(sel, "assistant")).toEqual({ messageId: "a1", text: "Entire reply." });
    });

    it("still rejects an overshoot that picks up a character of the next message", () => {
      const a1 = bubble("a1", "assistant", "First answer.");
      const a2 = bubble("a2", "assistant", "Second answer.");
      expect(selectionInMessage(selectRange(textOf(a1), 0, textOf(a2), 1), "assistant")).toBeNull();
    });

    it("still rejects a whole-bubble selection that includes its quote block", () => {
      const a1 = bubble("a1", "assistant", "Reply body", "quoted bit");
      expect(selectionInMessage(selectRange(a1, 0, a1, a1.childNodes.length), "assistant")).toBeNull();
    });

    it("still checks the role of the message the text is in", () => {
      const u1 = bubble("u1", "user", "My question.");
      const a1 = bubble("a1", "assistant", "Reply.");
      expect(selectionInMessage(selectRange(textOf(u1), 0, a1, 0), "assistant")).toBeNull();
    });
  });
});
