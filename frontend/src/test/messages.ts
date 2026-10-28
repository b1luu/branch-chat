import type { Message } from "../api/types.ts";

let seq = 0;

/** A Message with sensible defaults; override what the test cares about. */
export function msg(overrides: Partial<Message> & Pick<Message, "id">): Message {
  seq += 1;
  return {
    conversation_id: "c1",
    parent_id: null,
    role: "user",
    content: "",
    quote: null,
    created_at: new Date(Date.UTC(2026, 9, 28, 0, 0, seq)).toISOString(),
    ...overrides,
  };
}
