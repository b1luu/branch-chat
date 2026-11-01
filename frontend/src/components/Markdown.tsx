import type { ComponentPropsWithoutRef } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import "./Markdown.css";

/**
 * Links in model output open in a new tab and never get `window.opener`
 * (`noopener`) or a Referer header pointing back at the app (`noreferrer`).
 */
function ExternalLink({ href, title, children }: ComponentPropsWithoutRef<"a">) {
  return (
    <a href={href} title={title} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  );
}

const components: Components = { a: ExternalLink };

/**
 * Renders model-written Markdown (assistant replies, merge notes).
 *
 * Safe by default, which matters because the text comes from an LLM that may
 * echo whatever a user (or a pasted page) fed it:
 * - No raw HTML: react-markdown builds React elements from the Markdown AST and,
 *   without `rehype-raw`, never injects HTML, so `<script>` or
 *   `<img onerror=…>` shows up as inert text. No `dangerouslySetInnerHTML`.
 * - URLs go through react-markdown's default `urlTransform`, which drops
 *   `javascript:`/`data:` style links.
 *
 * Streaming: this re-parses the whole partial text on each token. That's fine
 * for chat-sized replies, and Markdown has no "unclosed" error state: an open
 * ``` fence mid-stream just renders the rest as a code block until it closes.
 */
export default function Markdown({ text }: { text: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
      {text}
    </ReactMarkdown>
  );
}
