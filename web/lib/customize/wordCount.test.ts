import { describe, expect, it } from "vitest";
import { WORD_COUNT_MAX, WORD_COUNT_MIN, countWords, wordBandState } from "./wordCount";

describe("countWords", () => {
  it("counts whitespace-separated words and collapses runs of spaces", () => {
    expect(countWords("hello world")).toBe(2);
    expect(countWords("  spaced   out\nwords ")).toBe(3);
  });

  it("treats empty / whitespace-only as zero", () => {
    expect(countWords("")).toBe(0);
    expect(countWords("   \n  ")).toBe(0);
  });
});

describe("wordBandState", () => {
  it("classifies counts against the 30-45 band", () => {
    expect(wordBandState(0)).toBe("empty");
    expect(wordBandState(WORD_COUNT_MIN - 1)).toBe("low");
    expect(wordBandState(WORD_COUNT_MIN)).toBe("ok");
    expect(wordBandState(WORD_COUNT_MAX)).toBe("ok");
    expect(wordBandState(WORD_COUNT_MAX + 1)).toBe("high");
  });
});
