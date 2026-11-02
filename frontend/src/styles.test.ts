/// <reference types="node" />
// Node types only for this file (the app's tsconfig is browser-only). Vite's
// `?raw` import doesn't work here: Vitest stubs CSS modules to empty strings.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// Vitest doesn't apply CSS to jsdom, so check the stylesheet as text: that the
// entry point loads it and that every class the components rely on is styled.
const read = (file: string) => readFileSync(new URL(file, import.meta.url), "utf8");
const css = read("./styles.css");
const mainSource = read("./main.tsx");

describe("styles.css", () => {
  it("is loaded by the app entry point", () => {
    expect(mainSource).toMatch(/^import "\.\/styles\.css";$/m);
  });

  it.each([
    ".app",
    ".chat",
    ".messages",
    ".message-user",
    ".message-assistant",
    ".composer",
    ".quote",
    ".note",
    ".note-label",
    ".markdown",
    ".tree",
    ".tree-node",
    ".tree-node.side-branch",
    ".tree-node.on-path",
    ".tree-node.active",
    ".branch-nav",
    ".error-notice",
    ".empty",
    ".char-count",
    ".composer-hint",
  ])("styles %s", (selector) => {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    expect(css).toMatch(new RegExp(`${escaped}[\\s,:{.\\[>]`));
  });

  it("has a dark theme, a narrow-screen layout and visible focus rings", () => {
    expect(css).toContain("@media (prefers-color-scheme: dark)");
    expect(css).toMatch(/@media \(max-width: 800px\)/);
    expect(css).toMatch(/:focus-visible\s*{[^}]*box-shadow/);
  });

  it("removes bullets from the tree", () => {
    expect(css).toMatch(/\.tree\s*{[^}]*list-style:\s*none/);
  });
});
