/**
 * Highlight `text` inside `container` (its first occurrence, within one text
 * node) the way a user drag would, via the real Selection/Range APIs.
 * Returns the Selection so tests can extend it.
 */
export function selectText(container: Element, text: string): Selection {
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const at = node.textContent?.indexOf(text) ?? -1;
    if (at === -1) continue;
    const range = document.createRange();
    range.setStart(node, at);
    range.setEnd(node, at + text.length);
    const selection = window.getSelection();
    if (!selection) throw new Error("no Selection API");
    selection.removeAllRanges();
    selection.addRange(range);
    return selection;
  }
  throw new Error(`text not found: ${text}`);
}

/** Highlight from the start of `fromText` to the end of `toText`, possibly across elements. */
export function selectAcross(from: Element, fromText: string, to: Element, toText: string): Selection {
  const find = (root: Element, text: string): [Node, number] => {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const at = n.textContent?.indexOf(text) ?? -1;
      if (at !== -1) return [n, at];
    }
    throw new Error(`text not found: ${text}`);
  };
  const [startNode, startAt] = find(from, fromText);
  const [endNode, endAt] = find(to, toText);
  const range = document.createRange();
  range.setStart(startNode, startAt);
  range.setEnd(endNode, endAt + toText.length);
  const selection = window.getSelection();
  if (!selection) throw new Error("no Selection API");
  selection.removeAllRanges();
  selection.addRange(range);
  return selection;
}
