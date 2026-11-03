/**
 * Code-point-aware string length and truncation.
 *
 * JS strings are UTF-16, so `s.length` counts an emoji like 🚀 as 2 and
 * `s.slice(0, n)` can cut between its two halves, leaving an unpaired
 * surrogate (rendered as �, and rejected by the backend with a 422). The
 * backend's limits (pydantic `max_length`) count code points, so the UI does too.
 */

const PAIR = /[\uD800-\uDBFF][\uDC00-\uDFFF]/g;

/** Number of code points (what Python's `len` returns for the same text). */
export function codePointLength(text: string): number {
  return text.length - (text.match(PAIR)?.length ?? 0);
}

/** The first `max` code points of `text`; never splits a surrogate pair. */
export function truncateCodePoints(text: string, max: number): string {
  if (text.length <= max) return text; // fast path: can't exceed max code points
  let units = 0;
  let points = 0;
  for (const ch of text) {
    if (points === max) break;
    units += ch.length;
    points += 1;
  }
  return text.slice(0, units);
}
