import { describe, expect, it } from "vitest";
import { sseResponse } from "../test/fetch.ts";
import { parseSSE, type SSEEvent } from "./sse.ts";

async function collect(chunks: string[]): Promise<SSEEvent[]> {
  const body = sseResponse(chunks).body;
  if (!body) throw new Error("no body");
  const out: SSEEvent[] = [];
  for await (const e of parseSSE(body)) out.push(e);
  return out;
}

describe("parseSSE", () => {
  it("parses named events", async () => {
    expect(await collect(["event: token\ndata: {\"text\":\"hi\"}\n\nevent: done\ndata: {}\n\n"])).toEqual([
      { event: "token", data: '{"text":"hi"}' },
      { event: "done", data: "{}" },
    ]);
  });

  it("reassembles events split across chunks, even mid-line", async () => {
    expect(await collect(["eve", "nt: tok", "en\ndata: {\"te", "xt\":\"a\"}\n", "\nevent: done\n", "data: 1\n\n"])).toEqual(
      [
        { event: "token", data: '{"text":"a"}' },
        { event: "done", data: "1" },
      ],
    );
  });

  it("handles CRLF line endings, including a CR/LF split across chunks", async () => {
    expect(await collect(["event: a\r\ndata: 1\r", "\n\r\n"])).toEqual([{ event: "a", data: "1" }]);
  });

  it("keeps the event name when a CRLF inside an event is split across chunks", async () => {
    // A lone trailing "\r" used to become "\n", and the next chunk's "\n" then made
    // a premature blank line, splitting `event:` from its `data:`.
    expect(await collect(["event: token\r", "\ndata: 1\r\n\r\n"])).toEqual([{ event: "token", data: "1" }]);
    expect(await collect(["event: a\r\ndata: 1\r", "\n", "\r", "\nevent: b\r\ndata: 2\r\n\r\n"])).toEqual([
      { event: "a", data: "1" },
      { event: "b", data: "2" },
    ]);
  });

  it("treats a bare CR as a line ending, even at the end of the stream", async () => {
    expect(await collect(["event: a\rdata: 1\r\r"])).toEqual([{ event: "a", data: "1" }]);
    expect(await collect(["event: a\rdata: 1\r"])).toEqual([{ event: "a", data: "1" }]);
  });

  it("joins multiple data lines and defaults the event name", async () => {
    expect(await collect(["data: line1\ndata: line2\n\n"])).toEqual([{ event: "message", data: "line1\nline2" }]);
  });

  it("ignores comments and blocks without data", async () => {
    expect(await collect([": keep-alive\n\nevent: ping\n\nevent: x\ndata: y\n\n"])).toEqual([{ event: "x", data: "y" }]);
  });

  it("emits a final event that lacks the trailing blank line", async () => {
    expect(await collect(["event: done\ndata: {}"])).toEqual([{ event: "done", data: "{}" }]);
  });

  it("decodes multi-byte UTF-8 split across chunks", async () => {
    const bytes = new TextEncoder().encode("data: héllo\n\n");
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(bytes.slice(0, 8)); // splits the 2-byte "é"
        c.enqueue(bytes.slice(8));
        c.close();
      },
    });
    const out: SSEEvent[] = [];
    for await (const e of parseSSE(body)) out.push(e);
    expect(out).toEqual([{ event: "message", data: "héllo" }]);
  });
});
