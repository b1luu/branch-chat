import { afterEach, describe, expect, it } from "vitest";
import { selectAcross, selectText } from "./test/select.ts";
import { selectionInMessage } from "./selection.ts";

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
});
