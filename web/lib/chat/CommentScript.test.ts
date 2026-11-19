import { describe, expect, it } from "vitest";
import { buildScript } from "./CommentScript";

describe("buildScript", () => {
  it("is deterministic for the same productId", () => {
    const a = buildScript({ productId: "prod-123" });
    const b = buildScript({ productId: "prod-123" });
    expect(a).toEqual(b);
  });

  it("produces different streams for different productIds", () => {
    const a = buildScript({ productId: "prod-A" });
    const b = buildScript({ productId: "prod-B" });
    // Not strictly different in every tick but the texts overall should
    // diverge — comparing one well-known position is enough.
    expect(a[0]).not.toEqual(b[0]);
  });

  it("front-loads density in the first 5s (opening hook)", () => {
    const ticks = buildScript({ productId: "front-load" });
    const opening = ticks.filter((t) => t.atMs < 5_000).length;
    const tail = ticks.filter((t) => t.atMs >= 15_000).length;
    expect(opening).toBeGreaterThan(tail);
  });

  it("emits messages across the full duration", () => {
    const ticks = buildScript({ productId: "spread", durationMs: 30_000 });
    expect(ticks.length).toBeGreaterThan(15);
    expect(Math.min(...ticks.map((t) => t.atMs))).toBeLessThan(2_000);
    expect(Math.max(...ticks.map((t) => t.atMs))).toBeGreaterThan(20_000);
  });

  it("yields ticks in chronological order", () => {
    const ticks = buildScript({ productId: "ordering" });
    for (let i = 1; i < ticks.length; i++) {
      expect(ticks[i].atMs).toBeGreaterThanOrEqual(ticks[i - 1].atMs);
    }
  });

  it("includes a group-excitement burst with distinct authors", () => {
    // Hard to assert exact burst placement without coupling to RNG;
    // instead, assert that *some* three consecutive ticks share a
    // recognisable burst phrase pattern (emoji-heavy or all-caps NEED).
    const ticks = buildScript({ productId: "burst-check" });
    let foundBurst = false;
    for (let i = 0; i < ticks.length - 2; i++) {
      const trio = ticks.slice(i, i + 3);
      const distinctAuthors = new Set(trio.map((t) => t.authorHandle)).size;
      if (distinctAuthors !== 3) continue;
      const trioText = trio.map((t) => t.text.toLowerCase()).join(" | ");
      const burstSignal =
        /fire|need|🔥|💸|cart|color/.test(trioText) && trio.every((t) => t.text.length > 0);
      if (burstSignal) {
        foundBurst = true;
        break;
      }
    }
    expect(foundBurst).toBe(true);
  });
});
