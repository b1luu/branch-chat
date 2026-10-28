// Mirrors the Pydantic response models in backend/app/api/schemas.py.
// Datetimes arrive as ISO-8601 strings.

export type Role = "user" | "assistant" | "system";

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  root_message_id: string | null;
}

export interface ConversationCreate {
  title?: string;
  system_prompt?: string;
}

export interface Message {
  id: string;
  conversation_id: string;
  parent_id: string | null;
  role: Role;
  content: string;
  quote: string | null;
  created_at: string;
}

export interface TreeNode {
  id: string;
  parent_id: string | null;
  role: Role;
  preview: string;
  created_at: string;
}

export interface Tree {
  conversation_id: string;
  nodes: TreeNode[];
}
