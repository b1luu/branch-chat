import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { controlledSSE, fetchCall, jsonResponse, mockFetch, sseEvent, sseResponse } from "../test/fetch.ts";
import { msg } from "../test/messages.ts";
import { selectText } from "../test/select.ts";
import type { Message } from "../api/types.ts";
import ChatView from "./ChatView.tsx";

afterEach(() => {
  vi.unstubAllGlobals();
  window.getSelection()?.removeAllRanges();
});

const root = msg({ id: "root", role: "system", content: "You are a helpful assistant." });
const u1 = msg({ id: "u1", parent_id: "root", content: "What is a CTE?" });
const a1 = msg({ id: "a1", parent_id: "u1", role: "assistant", content: "A common table expression." });
const u2 = msg({ id: "u2", parent_id: "a1", content: "Recursive?" });
const a2 = msg({ id: "a2", parent_id: "u2", role: "assistant", content: "Yes, it can recurse." });

function messages() {
  return within(screen.getByRole("list", { name: "Messages" })).queryAllByRole("listitem");
}

describe("ChatView", () => {
  it("renders the active path, hiding the system root", async () => {
    const fetchMock = mockFetch(jsonResponse([root, u1, a1]));
    render(<ChatView leafId="a1" onLeafChange={vi.fn()} />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading");

    await screen.findByText("What is a CTE?");
    expect(messages().map((li) => [li.dataset.role, li.textContent])).toEqual([
      ["user", "What is a CTE?"],
      ["assistant", "A common table expression."],
    ]);
    expect(screen.queryByText("You are a helpful assistant.")).not.toBeInTheDocument();
    expect(fetchCall(fetchMock)[0]).toBe("/api/messages/a1/path");
  });

  it("shows quotes on quoted user turns", async () => {
    mockFetch(jsonResponse([root, u1, a1, { ...u2, quote: "common table" }]));
    render(<ChatView leafId="u2" onLeafChange={vi.fn()} />);
    expect((await screen.findByText("common table")).tagName).toBe("BLOCKQUOTE");
  });

  it("shows an empty prompt for a fresh conversation", async () => {
    mockFetch(jsonResponse([root]));
    render(<ChatView leafId="root" onLeafChange={vi.fn()} />);
    expect(await screen.findByText("Send a message to start.")).toBeInTheDocument();
  });

  it("shows an error when the path can't load", async () => {
    mockFetch(jsonResponse({ detail: "Message not found" }, 404));
    render(<ChatView leafId="gone" onLeafChange={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Message not found");
  });

  it("streams a reply token by token and moves the leaf", async () => {
    const stream = controlledSSE();
    const fetchMock = mockFetch(jsonResponse([root, u1, a1]), stream.response);
    const onLeafChange = vi.fn();
    const ui = userEvent.setup();
    render(<ChatView leafId="a1" onLeafChange={onLeafChange} />);
    await screen.findByText("What is a CTE?");

    await ui.type(screen.getByRole("textbox", { name: "Message" }), "Recursive?");
    await ui.click(screen.getByRole("button", { name: "Send" }));

    // Optimistic user turn + empty streaming bubble; input cleared and send disabled.
    expect(await screen.findByText("Recursive?")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Message" })).toHaveValue("");
    expect(screen.getByRole("button", { name: "Sending…" })).toBeDisabled();

    stream.push(sseEvent("user", u2));
    stream.push(sseEvent("token", { text: "Yes, " }));
    await waitFor(() => expect(messages().at(-1)).toHaveTextContent("Yes,"));
    expect(messages().at(-1)).toHaveAttribute("aria-busy", "true");

    stream.push(sseEvent("token", { text: "it can recurse." }));
    await waitFor(() => expect(messages().at(-1)).toHaveTextContent("Yes, it can recurse."));

    stream.push(sseEvent("done", a2));
    stream.close();

    await waitFor(() => expect(onLeafChange).toHaveBeenCalledWith("a2"));
    expect(messages().map((li) => li.textContent)).toEqual([
      "What is a CTE?",
      "A common table expression.",
      "Recursive?",
      "Yes, it can recurse.",
    ]);
    expect(messages().at(-1)).not.toHaveAttribute("aria-busy");
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled(); // empty draft

    const [url, init] = fetchCall(fetchMock, 1);
    expect(url).toBe("/api/messages/a1/reply");
    expect(JSON.parse(init?.body as string)).toEqual({ content: "Recursive?" });
  });

  it("does not refetch the path for a leaf it just created", async () => {
    const fetchMock = mockFetch(
      jsonResponse([root, u1, a1]),
      sseResponse([sseEvent("user", u2), sseEvent("token", { text: "ok" }), sseEvent("done", a2)]),
    );
    const ui = userEvent.setup();
    const { rerender } = render(<ChatView leafId="a1" onLeafChange={vi.fn()} />);
    await screen.findByText("What is a CTE?");
    await ui.type(screen.getByRole("textbox", { name: "Message" }), "Recursive?{Enter}");
    await screen.findByText("Yes, it can recurse.");

    rerender(<ChatView leafId="a2" onLeafChange={vi.fn()} />);
    expect(screen.getByText("Yes, it can recurse.")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("loads the new path when the leaf changes externally", async () => {
    mockFetch(jsonResponse([root, u1, a1]), jsonResponse([root, u1, a1, u2, a2]));
    const { rerender } = render(<ChatView leafId="a1" onLeafChange={vi.fn()} />);
    await screen.findByText("What is a CTE?");
    rerender(<ChatView leafId="a2" onLeafChange={vi.fn()} />);
    expect(await screen.findByText("Yes, it can recurse.")).toBeInTheDocument();
  });

  it("Shift+Enter inserts a newline instead of sending", async () => {
    const fetchMock = mockFetch(jsonResponse([root]));
    const ui = userEvent.setup();
    render(<ChatView leafId="root" onLeafChange={vi.fn()} />);
    const box = await screen.findByRole("textbox", { name: "Message" });
    await ui.type(box, "line1{Shift>}{Enter}{/Shift}line2");
    expect(box).toHaveValue("line1\nline2");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not send blank messages", async () => {
    const fetchMock = mockFetch(jsonResponse([root]));
    const ui = userEvent.setup();
    render(<ChatView leafId="root" onLeafChange={vi.fn()} />);
    const box = await screen.findByRole("textbox", { name: "Message" });
    await ui.type(box, "   {Enter}");
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("shows a provider error, keeps the leaf, and restores the draft for retry", async () => {
    mockFetch(
      jsonResponse([root, u1, a1]),
      sseResponse([sseEvent("user", u2), sseEvent("error", { detail: "LLM provider error: timeout" })]),
    );
    const onLeafChange = vi.fn();
    const ui = userEvent.setup();
    render(<ChatView leafId="a1" onLeafChange={onLeafChange} />);
    await screen.findByText("What is a CTE?");
    await ui.type(screen.getByRole("textbox", { name: "Message" }), "Recursive?{Enter}");

    expect(await screen.findByRole("alert")).toHaveTextContent("LLM provider error: timeout");
    expect(screen.getByRole("textbox", { name: "Message" })).toHaveValue("Recursive?");
    expect(screen.getByRole("button", { name: "Send" })).toBeEnabled();
    expect(onLeafChange).not.toHaveBeenCalled();
  });

  it("shows an HTTP error from the reply endpoint", async () => {
    mockFetch(jsonResponse([root, u1, a1]), jsonResponse({ detail: "Message not found" }, 404));
    const ui = userEvent.setup();
    render(<ChatView leafId="a1" onLeafChange={vi.fn()} />);
    await screen.findByText("What is a CTE?");
    await ui.type(screen.getByRole("textbox", { name: "Message" }), "hi{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("Reply failed: Message not found");
  });
});

describe("ChatView — Ask about this", () => {
  const u3 = msg({ id: "u3", parent_id: "a1", content: "Why 'common'?", quote: "common table" });
  const a3 = msg({ id: "a3", parent_id: "u3", role: "assistant", content: "Because it is shared." });

  /** Highlight `text` in the message containing `inMessage` and release the mouse. */
  function highlight(inMessage: string, text: string) {
    const li = screen.getByText(inMessage).closest("li");
    if (!li) throw new Error("bubble not found");
    selectText(li, text);
    fireEvent.mouseUp(li);
    return li;
  }

  it("offers the button only for a highlight inside an assistant message", async () => {
    mockFetch(jsonResponse([root, u1, a1, u2, a2]));
    render(<ChatView leafId="a2" onLeafChange={vi.fn()} />);
    await screen.findByText("What is a CTE?");
    expect(screen.queryByRole("button", { name: "Ask about this" })).not.toBeInTheDocument();

    highlight("What is a CTE?", "CTE");
    expect(screen.queryByRole("button", { name: "Ask about this" })).not.toBeInTheDocument();

    const bubble = highlight("A common table expression.", "common table");
    expect(within(bubble).getByRole("button", { name: "Ask about this" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Ask about this" })).toHaveLength(1);

    // Clearing the highlight hides it again.
    window.getSelection()?.removeAllRanges();
    fireEvent.mouseUp(bubble);
    expect(screen.queryByRole("button", { name: "Ask about this" })).not.toBeInTheDocument();
  });

  it("creates a quote branch from the highlighted message, not the leaf", async () => {
    const stream = controlledSSE();
    const fetchMock = mockFetch(jsonResponse([root, u1, a1, u2, a2]), stream.response);
    const onLeafChange = vi.fn();
    const ui = userEvent.setup();
    render(<ChatView leafId="a2" onLeafChange={onLeafChange} />);
    await screen.findByText("What is a CTE?");

    highlight("A common table expression.", "common table");
    await ui.click(screen.getByRole("button", { name: "Ask about this" }));

    // Quote shows above the composer, which gets focus; the highlight is consumed.
    const draft = screen.getByLabelText("Asking about");
    expect(within(draft).getByText("common table").tagName).toBe("BLOCKQUOTE");
    const box = screen.getByRole("textbox", { name: "Message" });
    expect(box).toHaveFocus();
    expect(box).toHaveAttribute("placeholder", "Ask about the quoted text…");
    expect(screen.queryByRole("button", { name: "Ask about this" })).not.toBeInTheDocument();

    await ui.type(box, "Why 'common'?{Enter}");

    // While streaming, the turns below the quoted message are out of view.
    await waitFor(() => expect(screen.queryByText("Recursive?")).not.toBeInTheDocument());
    const [url, init] = fetchCall(fetchMock, 1);
    expect(url).toBe("/api/messages/a1/reply");
    expect(JSON.parse(init?.body as string)).toEqual({ content: "Why 'common'?", quote: "common table" });

    stream.push(sseEvent("user", u3));
    stream.push(sseEvent("token", { text: "Because it is shared." }));
    stream.push(sseEvent("done", a3));
    stream.close();

    await waitFor(() => expect(onLeafChange).toHaveBeenCalledWith("a3"));
    expect(messages().map((li) => li.textContent)).toEqual([
      "What is a CTE?",
      "A common table expression.",
      "common tableWhy 'common'?",
      "Because it is shared.",
    ]);
    expect(screen.queryByLabelText("Asking about")).not.toBeInTheDocument();
  });

  it("cancelling the quote sends a normal reply under the leaf", async () => {
    const fetchMock = mockFetch(
      jsonResponse([root, u1, a1]),
      sseResponse([sseEvent("user", u2), sseEvent("done", a2)]),
    );
    const ui = userEvent.setup();
    render(<ChatView leafId="a1" onLeafChange={vi.fn()} />);
    await screen.findByText("What is a CTE?");

    highlight("A common table expression.", "expression");
    await ui.click(screen.getByRole("button", { name: "Ask about this" }));
    await ui.click(screen.getByRole("button", { name: "Cancel quote" }));
    expect(screen.queryByLabelText("Asking about")).not.toBeInTheDocument();

    await ui.type(screen.getByRole("textbox", { name: "Message" }), "Recursive?{Enter}");
    await screen.findByText("Yes, it can recurse.");
    const [url, init] = fetchCall(fetchMock, 1);
    expect(url).toBe("/api/messages/a1/reply");
    expect(JSON.parse(init?.body as string)).toEqual({ content: "Recursive?" });
  });

  it("keeps the quote and the original path when the reply fails", async () => {
    mockFetch(
      jsonResponse([root, u1, a1, u2, a2]),
      sseResponse([sseEvent("user", u3), sseEvent("error", { detail: "LLM provider error: boom" })]),
    );
    const onLeafChange = vi.fn();
    const ui = userEvent.setup();
    render(<ChatView leafId="a2" onLeafChange={onLeafChange} />);
    await screen.findByText("What is a CTE?");

    highlight("A common table expression.", "common table");
    await ui.click(screen.getByRole("button", { name: "Ask about this" }));
    await ui.type(screen.getByRole("textbox", { name: "Message" }), "Why?{Enter}");

    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    expect(screen.getByText("Recursive?")).toBeInTheDocument();
    expect(within(screen.getByLabelText("Asking about")).getByText("common table")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Message" })).toHaveValue("Why?");
    expect(onLeafChange).not.toHaveBeenCalled();
  });

  it("drops a pending quote when the active leaf changes", async () => {
    mockFetch(jsonResponse([root, u1, a1]), jsonResponse([root, u1, a1, u2, a2]));
    const ui = userEvent.setup();
    const { rerender } = render(<ChatView leafId="a1" onLeafChange={vi.fn()} />);
    await screen.findByText("What is a CTE?");
    highlight("A common table expression.", "common");
    await ui.click(screen.getByRole("button", { name: "Ask about this" }));
    expect(screen.getByLabelText("Asking about")).toBeInTheDocument();

    rerender(<ChatView leafId="a2" onLeafChange={vi.fn()} />);
    await screen.findByText("Yes, it can recurse.");
    expect(screen.queryByLabelText("Asking about")).not.toBeInTheDocument();
  });
});

describe("ChatView — merged notes", () => {
  const note = {
    id: "n1",
    message_id: "a1",
    source_leaf_id: "b9",
    content: "Side branch concluded: CTE means common table expression.",
    created_at: a1.created_at,
  };

  it("renders notes under the message they are pinned to", async () => {
    mockFetch(jsonResponse([root, u1, { ...a1, notes: [note] }, { ...u2, notes: [] }, a2]));
    render(<ChatView leafId="a2" onLeafChange={vi.fn()} />);
    const aside = await screen.findByRole("complementary", { name: "Merged note" });
    expect(aside).toHaveTextContent("Merged from a side branch");
    expect(aside).toHaveTextContent(note.content);
    expect(aside.closest("li")).toHaveTextContent("A common table expression.");
    expect(screen.getAllByRole("complementary", { name: "Merged note" })).toHaveLength(1);
  });

  it("does not offer Ask about this for text inside a note", async () => {
    mockFetch(jsonResponse([root, u1, { ...a1, notes: [note] }]));
    render(<ChatView leafId="a1" onLeafChange={vi.fn()} />);
    const aside = await screen.findByRole("complementary", { name: "Merged note" });
    selectText(aside, "concluded");
    fireEvent.mouseUp(aside);
    expect(screen.queryByRole("button", { name: "Ask about this" })).not.toBeInTheDocument();
  });
});

describe("ChatView — navigating away mid-stream", () => {
  // Another branch / conversation the user clicks while a reply is still streaming.
  const ub = msg({ id: "ub", parent_id: "root", content: "Other branch question" });
  const ab = msg({ id: "ab", parent_id: "ub", role: "assistant", content: "Other branch answer" });

  async function startStreaming(onLeafChange: (id: string) => void, onReplyPersisted?: (m: Message) => void) {
    const stream = controlledSSE();
    const fetchMock = mockFetch(jsonResponse([root, u1, a1]), stream.response, jsonResponse([root, ub, ab]));
    const ui = userEvent.setup();
    const view = render(<ChatView leafId="a1" onLeafChange={onLeafChange} onReplyPersisted={onReplyPersisted} />);
    await screen.findByText("What is a CTE?");
    await ui.type(screen.getByRole("textbox", { name: "Message" }), "Recursive?{Enter}");
    stream.push(sseEvent("user", u2));
    stream.push(sseEvent("token", { text: "Yes, " }));
    await waitFor(() => expect(messages().at(-1)).toHaveTextContent("Yes,"));
    return { stream, fetchMock, ui, view };
  }

  it("shows the newly selected path, not the old branch's streaming turn", async () => {
    const onLeafChange = vi.fn();
    const { view } = await startStreaming(onLeafChange);

    view.rerender(<ChatView leafId="ab" onLeafChange={onLeafChange} />);
    await screen.findByText("Other branch answer");
    expect(messages().map((li) => li.textContent)).toEqual(["Other branch question", "Other branch answer"]);
    expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument(); // not "Sending…"
  });

  it("lets the old reply finish in the background without hijacking the view", async () => {
    const onLeafChange = vi.fn();
    const { stream, fetchMock, view } = await startStreaming(onLeafChange);

    view.rerender(<ChatView leafId="ab" onLeafChange={onLeafChange} />);
    await screen.findByText("Other branch answer");
    stream.push(sseEvent("token", { text: "it can recurse." }));
    stream.push(sseEvent("done", a2));
    stream.close();

    // Give the finished stream a chance to (wrongly) update state.
    await new Promise((r) => setTimeout(r, 20));
    expect(onLeafChange).not.toHaveBeenCalled();
    expect(messages().map((li) => li.textContent)).toEqual(["Other branch question", "Other branch answer"]);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    // Not aborted: the server still gets to persist the reply the user asked for.
    const [, init] = fetchCall(fetchMock, 1);
    expect(init?.signal?.aborted).toBe(false);
  });

  it("doesn't surface the old reply's failure or restore its draft on the new branch", async () => {
    const onLeafChange = vi.fn();
    const { stream, view } = await startStreaming(onLeafChange);

    view.rerender(<ChatView leafId="ab" onLeafChange={onLeafChange} />);
    await screen.findByText("Other branch answer");
    stream.push(sseEvent("error", { detail: "model crashed" }));
    stream.close();

    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Message" })).toHaveValue("");
  });

  it("can send on the new branch while the old reply is still streaming", async () => {
    const onLeafChange = vi.fn();
    const { fetchMock, ui, view } = await startStreaming(onLeafChange);
    const ub2 = msg({ id: "ub2", parent_id: "ab", content: "Follow-up" });
    const ab2 = msg({ id: "ab2", parent_id: "ub2", role: "assistant", content: "Follow-up answer" });
    fetchMock.mockResolvedValueOnce(
      sseResponse([sseEvent("user", ub2), sseEvent("token", { text: "Follow-up answer" }), sseEvent("done", ab2)]),
    );

    view.rerender(<ChatView leafId="ab" onLeafChange={onLeafChange} />);
    await screen.findByText("Other branch answer");
    await ui.type(screen.getByRole("textbox", { name: "Message" }), "Follow-up{Enter}");

    await waitFor(() => expect(onLeafChange).toHaveBeenCalledWith("ab2"));
    expect(fetchCall(fetchMock, 3)[0]).toBe("/api/messages/ab/reply");
    expect(messages().map((li) => li.textContent)).toEqual([
      "Other branch question",
      "Other branch answer",
      "Follow-up",
      "Follow-up answer",
    ]);
  });

  // Q4: the tree sidebar learns about a reply that finished after the user navigated away.
  it("reports a background reply as persisted even though it's detached", async () => {
    const onLeafChange = vi.fn();
    const onReplyPersisted = vi.fn();
    const { stream, view } = await startStreaming(onLeafChange, onReplyPersisted);

    view.rerender(<ChatView leafId="ab" onLeafChange={onLeafChange} onReplyPersisted={onReplyPersisted} />);
    await screen.findByText("Other branch answer");
    expect(onReplyPersisted).not.toHaveBeenCalled();
    stream.push(sseEvent("done", a2));
    stream.close();

    await waitFor(() => expect(onReplyPersisted).toHaveBeenCalledWith(a2));
    expect(onReplyPersisted).toHaveBeenCalledTimes(1);
    expect(onLeafChange).not.toHaveBeenCalled(); // still detached: the view isn't hijacked
  });

  it("reports an attached reply as persisted too, and a failed one not at all", async () => {
    const onLeafChange = vi.fn();
    const onReplyPersisted = vi.fn();
    const { stream, fetchMock } = await startStreaming(onLeafChange, onReplyPersisted);
    stream.push(sseEvent("done", a2));
    stream.close();
    await waitFor(() => expect(onLeafChange).toHaveBeenCalledWith("a2"));
    expect(onReplyPersisted).toHaveBeenCalledExactlyOnceWith(a2);

    onReplyPersisted.mockClear();
    const failing = controlledSSE();
    fetchMock.mockReset(); // drop startStreaming's unused queued path response
    fetchMock.mockResolvedValueOnce(failing.response);
    await userEvent.setup().type(screen.getByRole("textbox", { name: "Message" }), "Again{Enter}");
    failing.push(sseEvent("error", { detail: "model crashed" }));
    failing.close();
    expect(await screen.findByRole("alert")).toHaveTextContent("model crashed");
    expect(onReplyPersisted).not.toHaveBeenCalled();
  });
});
