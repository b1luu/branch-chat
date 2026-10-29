/**
 * Minimal Server-Sent Events parser for `fetch` response bodies.
 *
 * Why not `EventSource`? It only does GET, and our reply endpoint is a POST with
 * a JSON body. So we read the body stream ourselves and split it into events.
 *
 * SSE framing: an event is a block of `field: value` lines ended by a blank line.
 * We care about `event:` (the name, default "message") and `data:` (joined with
 * "\n" if repeated). Network chunks can split anywhere, even mid-line, so we
 * buffer until we see a complete blank-line-terminated block.
 */
export interface SSEEvent {
  event: string;
  data: string;
}

function parseBlock(block: string): SSEEvent | null {
  let event = "message";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line === "" || line.startsWith(":")) continue; // blank or comment
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }
  return data.length === 0 ? null : { event, data: data.join("\n") };
}

export async function* parseSSE(body: ReadableStream<Uint8Array>): AsyncGenerator<SSEEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  // A chunk ending in "\r" might be the first half of a CRLF. Normalizing it to
  // "\n" right away would turn the next chunk's "\n" into a second line break
  // (a fake blank line that ends the event early), so hold it back until the
  // next chunk shows what follows.
  let pendingCR = false;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      let text: string = (pendingCR ? "\r" : "") + decoder.decode(value, { stream: true });
      pendingCR = text.endsWith("\r");
      if (pendingCR) text = text.slice(0, -1);
      buffer += text.replace(/\r\n?/g, "\n");
      let end: number;
      while ((end = buffer.indexOf("\n\n")) !== -1) {
        const parsed = parseBlock(buffer.slice(0, end));
        buffer = buffer.slice(end + 2);
        if (parsed) yield parsed;
      }
    }
    // A final event without the trailing blank line still counts.
    const parsed = parseBlock(buffer + decoder.decode().replace(/\r\n?/g, "\n"));
    if (parsed) yield parsed;
  } finally {
    reader.releaseLock();
  }
}
