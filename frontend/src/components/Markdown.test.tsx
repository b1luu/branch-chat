import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { controlledSSE, fetchCall, jsonResponse, mockFetch, sseEvent } from "../test/fetch.ts";
import { msg } from "../test/messages.ts";
import { selectAcross } from "../test/select.ts";
import ChatView from "./ChatView.tsx";
import Markdown from "./Markdown.tsx";

afterEach(() => {
  vi.unstubAllGlobals();
  window.getSelection()?.removeAllRanges();
  delete (window as { pwned?: boolean }).pwned;
});

const root = msg({ id: "root", role: "system", content: "sys" });
const u1 = msg({ id: "u1", parent_id: "root", content: "Explain CTEs" });

function bubble(id: string): HTMLElement {
  const li = document.querySelector<HTMLElement>(`[data-message-id="${id}"]`);
  if (!li) throw new Error(`bubble ${id} not found`);
  return li;
}

async function renderPath(...path: ReturnType<typeof msg>[]) {
  const leaf = path.at(-1);
  if (!leaf) throw new Error("empty path");
  mockFetch(jsonResponse([root, ...path]));
  render(<ChatView leafId={leaf.id} onLeafChange={vi.fn()} />);
  await screen.findByText("Explain CTEs");
}

describe("Markdown", () => {
  it("renders headings, emphasis, lists, inline code and fenced code as elements", () => {
    const { container } = render(
      <Markdown
        text={[
          "## Recursive CTEs",
          "",
          "A **CTE** is *named*. Use `WITH RECURSIVE`.",
          "",
          "- anchor",
          "- step",
          "",
          "1. first",
          "2. second",
          "",
          "```sql",
          "SELECT 1;",
          "  SELECT 2;",
          "```",
        ].join("\n")}
      />,
    );
    expect(screen.getByRole("heading", { level: 2, name: "Recursive CTEs" })).toBeInTheDocument();
    expect(screen.getByText("CTE").tagName).toBe("STRONG");
    expect(screen.getByText("named").tagName).toBe("EM");
    expect(screen.getByText("WITH RECURSIVE").tagName).toBe("CODE");
    expect(container.querySelector("ul")?.children).toHaveLength(2);
    expect(container.querySelector("ol")?.children).toHaveLength(2);
    const pre = container.querySelector("pre");
    expect(pre?.firstElementChild?.tagName).toBe("CODE");
    // Whitespace inside the block is kept as-is.
    expect(pre?.textContent).toBe("SELECT 1;\n  SELECT 2;\n");
  });

  it("keeps raw HTML inert: shown as text, never turned into elements", () => {
    const evil = '<script>window.pwned = true</script>\n\n<img src="x" onerror="window.pwned = true">';
    const { container } = render(<Markdown text={evil} />);
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("[onerror]")).toBeNull();
    expect(container.textContent).toContain("<script>window.pwned = true</script>");
    expect(container.textContent).toContain('<img src="x" onerror="window.pwned = true">');
    expect((window as { pwned?: boolean }).pwned).toBeUndefined();
  });

  it("opens links in a new tab without opener/referrer and drops javascript: URLs", () => {
    render(<Markdown text={"[docs](https://example.com/cte) and [bad](javascript:alert(1))"} />);
    const docs = screen.getByRole("link", { name: "docs" });
    expect(docs).toHaveAttribute("href", "https://example.com/cte");
    expect(docs).toHaveAttribute("target", "_blank");
    expect(docs).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByText("bad").getAttribute("href") ?? "").not.toMatch(/javascript:/i);
  });

  it("renders merge notes as Markdown too", async () => {
    const a1 = msg({
      id: "a1",
      parent_id: "u1",
      role: "assistant",
      content: "Answer.",
      notes: [{ id: "n1", message_id: "a1", source_leaf_id: "x", content: "We found **two** options.", created_at: "2026-10-28T00:00:00Z" }],
    });
    await renderPath(u1, a1);
    const note = screen.getByRole("complementary", { name: "Merged note" });
    expect(within(note).getByText("two").tagName).toBe("STRONG");
  });
});

describe("ChatView — Markdown rendering", () => {
  it("keeps line breaks in user turns as plain text (no Markdown)", async () => {
    const u2 = msg({ id: "u2", parent_id: "u1", content: "line one\nline two\n**not bold**" });
    await renderPath(u1, u2);
    const content = bubble("u2").querySelector<HTMLElement>(".content");
    expect(content?.textContent).toBe("line one\nline two\n**not bold**");
    expect(content).toHaveStyle({ whiteSpace: "pre-wrap" });
    expect(content?.querySelector("strong")).toBeNull();
  });

  it("renders a partial code fence while streaming, then the closed block", async () => {
    const stream = controlledSSE();
    mockFetch(jsonResponse([root, u1]), stream.response);
    const ui = userEvent.setup();
    render(<ChatView leafId="u1" onLeafChange={vi.fn()} />);
    await screen.findByText("Explain CTEs");
    // u1 is a question, so this asks in its place; the stream is what we care about.
    await ui.type(screen.getByRole("textbox", { name: "Message" }), "Show SQL{Enter}");

    const last = () => {
      const items = within(screen.getByRole("list", { name: "Messages" })).getAllByRole("listitem");
      const li = items.at(-1);
      if (!li) throw new Error("no messages");
      return li;
    };
    stream.push(sseEvent("token", { text: "Here:\n\n```sql\nWITH RECURSIVE t AS (" }));
    await waitFor(() => expect(last().querySelector("pre code")).toHaveTextContent("WITH RECURSIVE t AS ("));
    expect(last()).toHaveAttribute("aria-busy", "true");
    expect(within(last()).getByText("Here:").tagName).toBe("P");

    stream.push(sseEvent("token", { text: "\n  SELECT 1\n)\n```\n\nDone." }));
    await waitFor(() => expect(within(last()).getByText("Done.").tagName).toBe("P"));
    expect(last().querySelector("pre code")?.textContent).toBe("WITH RECURSIVE t AS (\n  SELECT 1\n)\n");
  });

  it("quotes the visible text of a selection that spans formatting", async () => {
    const a1 = msg({ id: "a1", parent_id: "u1", role: "assistant", content: "Use **recursive** CTEs for `parent_id` chains." });
    const stream = controlledSSE();
    const fetchMock = mockFetch(jsonResponse([root, u1, a1]), stream.response);
    const ui = userEvent.setup();
    render(<ChatView leafId="a1" onLeafChange={vi.fn()} />);
    await screen.findByText("Explain CTEs");

    const li = bubble("a1");
    const paragraph = li.querySelector(".content p");
    if (!paragraph) throw new Error("no paragraph");
    selectAcross(paragraph, "Use", paragraph, "parent_id");
    fireEvent.mouseUp(li);
    await ui.click(within(li).getByRole("button", { name: "Ask about this" }));

    const draft = screen.getByLabelText("Asking about");
    expect(within(draft).getByText("Use recursive CTEs for parent_id")).toBeInTheDocument();
    await ui.type(screen.getByRole("textbox", { name: "Message" }), "Why?{Enter}");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const [url, init] = fetchCall(fetchMock, 1);
    expect(url).toBe("/api/messages/a1/reply");
    expect(JSON.parse(init?.body as string)).toEqual({ content: "Why?", quote: "Use recursive CTEs for parent_id" });
  });
});
