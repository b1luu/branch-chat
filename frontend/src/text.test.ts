import { describe, expect, it } from "vitest";
import { codePointLength, truncateCodePoints } from "./text.ts";

const ROCKET = "\u{1F680}"; // 2 UTF-16 units, 1 code point

/** True if `s` has a high surrogate without a low one after it, or vice versa. */
function hasLoneSurrogate(s: string): boolean {
  return /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/.test(s);
}

describe("codePointLength", () => {
  it("counts code points, like Python's len", () => {
    expect(codePointLength("")).toBe(0);
    expect(codePointLength("abc")).toBe(3);
    expect(codePointLength(ROCKET)).toBe(1);
    expect(codePointLength(`a${ROCKET}b${ROCKET}`)).toBe(4);
    expect(codePointLength("é")).toBe(1); // BMP, one unit
  });
});

describe("truncateCodePoints", () => {
  it("returns short text unchanged", () => {
    expect(truncateCodePoints("abc", 5)).toBe("abc");
    expect(truncateCodePoints(ROCKET.repeat(3), 3)).toBe(ROCKET.repeat(3));
  });

  it("never splits a surrogate pair at the cut", () => {
    // `.slice(0, 2)` here would end on the rocket's high half.
    const text = `a${ROCKET}bc`;
    expect(text.slice(0, 2)).toHaveLength(2);
    expect(hasLoneSurrogate(text.slice(0, 2))).toBe(true);
    expect(truncateCodePoints(text, 2)).toBe(`a${ROCKET}`);
    expect(truncateCodePoints(text, 1)).toBe("a");
    for (let max = 0; max <= 5; max += 1) {
      expect(hasLoneSurrogate(truncateCodePoints(text, max))).toBe(false);
    }
  });

  it("keeps `max` code points of emoji-only text", () => {
    const text = ROCKET.repeat(10);
    expect(truncateCodePoints(text, 4)).toBe(ROCKET.repeat(4));
    expect(codePointLength(truncateCodePoints(text, 4))).toBe(4);
    expect(truncateCodePoints(text, 0)).toBe("");
  });
});
