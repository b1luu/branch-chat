import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { controlledSSE, fetchCall, jsonResponse, mockFetch, sseEvent, sseResponse } from "../test/fetch.ts";
import { msg } from "../test/messages.ts";
import ChatView from "./ChatView.tsx";

afterEach(() => {
  vi.unstubAllGlobals();
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
