// Mirrors `server/tests/broadcast/test_timeline.py`: the server and the
// player must agree on what is on air.

import { describe, expect, it } from "vitest";
import {
  first,
  following,
  jumpTo,
  locate,
  makeTimeline,
  type Phase,
  type Playhead,
  type Position,
  preceding,
  reanchor,
  totalMs,
  windDown,
} from "./timeline";

const A = { id: "a", lengthMs: 10_000 };
const B = { id: "b", lengthMs: 20_000 };
const C = { id: "c", lengthMs: 5_000 };
const INTRO = { id: "intro", lengthMs: 5_000 };
const OUTRO = { id: "outro", lengthMs: 4_000 };

function head(itemId: string, opts: Partial<Playhead> & { phase?: Phase } = {}): Playhead {
  return {
    phase: "body",
    index: 0,
    cycle: 0,
    offsetMs: 0,
    atMs: 0,
    playing: true,
    itemId,
    ...opts,
  };
}

function where(p: Position | null) {
  return p ? [p.phase, p.itemId, p.cycle, p.offsetMs] : null;
}

describe("broadcast timeline", () => {
  it("loops forever and skips whole cycles", () => {
    const tl = makeTimeline({ items: [A, B] });
    const start = head("a");
    expect(where(locate(tl, start, 5_000))).toEqual(["body", "a", 0, 5_000]);
    expect(where(locate(tl, start, 10_000))).toEqual(["body", "b", 0, 0]);
    expect(where(locate(tl, start, 30_000))).toEqual(["body", "a", 1, 0]);
    expect(where(locate(tl, start, 3_605_000))).toEqual(["body", "a", 120, 5_000]);
    expect(totalMs(tl)).toBeNull();
  });

  it("plays intro, passes, outro, then ends in count mode", () => {
    const tl = makeTimeline({
      items: [A, B],
      intro: INTRO,
      outro: OUTRO,
      loopMode: "count",
      loopCount: 2,
    });
    const begin = first(tl);
    expect(begin?.phase).toBe("intro");
    const start = head("intro", { phase: "intro" });
    expect(where(locate(tl, start, 5_000))).toEqual(["body", "a", 0, 0]);
    expect(where(locate(tl, start, 35_000))).toEqual(["body", "a", 1, 0]);
    expect(where(locate(tl, start, 65_000))).toEqual(["outro", "outro", 2, 0]);
    expect(locate(tl, start, 69_000)).toBeNull();
    expect(totalMs(tl)).toBe(69_000);
  });

  it("keeps a paused playhead in place", () => {
    const tl = makeTimeline({ items: [A, B] });
    const paused = head("b", { index: 1, offsetMs: 7_000, playing: false });
    expect(where(locate(tl, paused, 999_999))).toEqual(["body", "b", 0, 7_000]);
  });

  it("moves to the following and preceding slots", () => {
    const tl = makeTimeline({
      items: [A, B],
      intro: INTRO,
      outro: OUTRO,
      loopMode: "count",
      loopCount: 2,
    });
    const onA = locate(tl, head("a"), 1_000)!;
    const onB = locate(tl, head("b", { index: 1 }), 1_000)!;
    const onBLast = locate(tl, head("b", { index: 1, cycle: 1 }), 1_000)!;
    const onOutro = locate(tl, head("outro", { phase: "outro", cycle: 2 }), 1_000)!;
    expect(where(following(tl, onA))).toEqual(["body", "b", 0, 0]);
    expect(where(following(tl, onB))).toEqual(["body", "a", 1, 0]);
    expect(where(following(tl, onBLast))).toEqual(["outro", "outro", 2, 0]);
    expect(following(tl, onOutro)).toBeNull();
    expect(where(preceding(tl, onA))).toEqual(["intro", "intro", 0, 0]);
    expect(where(preceding(tl, onOutro))).toEqual(["body", "b", 1, 0]);
  });

  it("jumps within the current pass, clamped in count mode", () => {
    const tl = makeTimeline({ items: [A, B], outro: OUTRO, loopMode: "count", loopCount: 2 });
    const onA = locate(tl, head("a", { cycle: 1 }), 0)!;
    const onOutro = locate(tl, head("outro", { phase: "outro", cycle: 2 }), 0)!;
    expect(where(jumpTo(tl, "b", onA))).toEqual(["body", "b", 1, 0]);
    expect(where(jumpTo(tl, "a", onOutro))).toEqual(["body", "a", 1, 0]);
    expect(jumpTo(tl, "missing", onA)).toBeNull();
  });

  it("winds down to the outro or ends", () => {
    const tl = makeTimeline({ items: [A, B], outro: OUTRO });
    const onB = locate(tl, head("b", { index: 1, cycle: 3 }), 0)!;
    expect(where(windDown(tl, onB))).toEqual(["outro", "outro", 4, 0]);
    const plain = makeTimeline({ items: [A, B] });
    expect(windDown(plain, locate(plain, head("a"), 0)!)).toBeNull();
  });

  it("re-anchors so an edit keeps the current video on air", () => {
    const anchored = reanchor(makeTimeline({ items: [A, B, C] }), head("a"), 12_000)!;
    expect([anchored.itemId, anchored.offsetMs, anchored.atMs]).toEqual(["b", 2_000, 12_000]);
    const after = makeTimeline({ items: [C, B] });
    expect(where(locate(after, anchored, 13_000))).toEqual(["body", "b", 0, 3_000]);
  });

  it("hands a removed video's slot to its successor", () => {
    const playingB = head("b", { index: 1, offsetMs: 5_000 });
    expect(where(locate(makeTimeline({ items: [A, C] }), playingB, 1_000))).toEqual([
      "body",
      "c",
      0,
      1_000,
    ]);
    expect(where(locate(makeTimeline({ items: [A] }), playingB, 1_000))).toEqual([
      "body",
      "a",
      1,
      1_000,
    ]);
  });

  it("finishes the current pass when loopCount is lowered", () => {
    const tl = makeTimeline({ items: [A, B], outro: OUTRO, loopMode: "count", loopCount: 2 });
    const late = head("a", { cycle: 3 });
    expect(where(locate(tl, late, 15_000))).toEqual(["body", "b", 3, 5_000]);
    expect(where(locate(tl, late, 30_000))).toEqual(["outro", "outro", 4, 0]);
  });

  it("does not borrow time from a shortened slot", () => {
    const tl = makeTimeline({ items: [{ id: "a", lengthMs: 5_000 }, B] });
    expect(where(locate(tl, head("a", { offsetMs: 8_000 }), 1_000))).toEqual([
      "body",
      "b",
      0,
      1_000,
    ]);
  });
});
