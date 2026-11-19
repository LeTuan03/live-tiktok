import { describe, expect, it } from "vitest";
import {
  adaptBroadcast,
  clockOffset,
  newerBroadcast,
  parseBroadcastEvent,
  type WireItem,
} from "./api";
import { applyLocal } from "./control";
import { drift, planPlayer } from "./engine";
import {
  buildTimeline,
  durationChoices,
  formatClock,
  formatSpan,
  itemsById,
  playsInSlot,
  schedule,
  versionNumbers,
} from "./logic";
import { locate, makeTimeline } from "./timeline";

process.env.NEXT_PUBLIC_API_BASE_URL = "http://api.test";

function wireItem(id: string, over: Partial<WireItem> = {}): WireItem {
  return {
    id,
    role: "product",
    position: 0,
    source: "promo",
    source_ref: `job-${id}`,
    title: `Video ${id}`,
    duration_s: null,
    video_s: 20,
    slot_s: 20,
    playable: true,
    video_url: `/promo/videos/job-${id}/file?v=1`,
    ...over,
  };
}

function wireBroadcast(items: WireItem[], extra: Record<string, unknown> = {}) {
  return {
    id: "b1",
    title: "Phiên thử",
    loop_mode: "continuous",
    loop_count: 1,
    items,
    cycle_s: 0,
    playback: { status: "idle", server_time_ms: 1_000 },
    created_at: "2026-11-19T00:00:00Z",
    updated_at: "2026-11-19T00:00:00Z",
    ...extra,
  };
}

describe("broadcast api adapters", () => {
  it("resolves media URLs against the API base and adapts the playhead", () => {
    const b = adaptBroadcast(
      wireBroadcast([wireItem("a")], {
        playback: {
          status: "live",
          server_time_ms: 5_000,
          playhead: { phase: "body", item_id: "a", offset_ms: 0, at_ms: 1_000, playing: true },
        },
      }),
      250,
    );
    expect(b.items[0].videoUrl).toBe("http://api.test/promo/videos/job-a/file?v=1");
    expect(b.playback.playhead).toMatchObject({ itemId: "a", atMs: 1_000, index: 0, cycle: 0 });
    expect(b.clockOffsetMs).toBe(250);
  });

  it("measures the clock offset mid-flight and keeps the newer snapshot", () => {
    expect(clockOffset(10_100, 10_000, 10_000)).toBe(100);
    expect(clockOffset(10_000, 9_000, 9_200)).toBe(900);
    const older = adaptBroadcast(wireBroadcast([]));
    const newer = adaptBroadcast(
      wireBroadcast([], { playback: { status: "live", server_time_ms: 9_000 } }),
    );
    expect(newerBroadcast(newer, older)).toBe(newer);
    expect(newerBroadcast(older, newer)).toBe(newer);
    expect(parseBroadcastEvent("not json", 0)).toBeNull();
    expect(parseBroadcastEvent(JSON.stringify(wireBroadcast([])), 400)?.clockOffsetMs).toBe(600);
  });
});

describe("broadcast logic", () => {
  const broadcast = adaptBroadcast(
    wireBroadcast(
      [
        wireItem("a"),
        wireItem("b", { playable: false, slot_s: null, problem: "Video đang được tạo lại." }),
        wireItem("c", { duration_s: 45, slot_s: 45 }),
      ],
      { intro: wireItem("i", { role: "intro", video_s: 5, slot_s: 5 }) },
    ),
  );

  it("builds the same playable timeline as the server", () => {
    const tl = buildTimeline(broadcast);
    expect(tl.items.map((s) => s.id)).toEqual(["a", "c"]);
    expect(tl.intro?.lengthMs).toBe(5_000);
  });

  it("lists the play order with start times and skips what cannot play", () => {
    expect(schedule(broadcast).map((r) => [r.item.id, r.startMs, r.lengthMs])).toEqual([
      ["i", 0, 5_000],
      ["a", 5_000, 20_000],
      ["b", null, null],
      ["c", 25_000, 45_000],
    ]);
  });

  it("offers whole repeats and presets as time on air", () => {
    const choices = durationChoices(26.27, 75);
    expect(choices[0]).toEqual({ value: null, label: "1 lần (26 giây)" });
    const values = choices.map((c) => c.value);
    expect(values).toContain(53);
    expect(values).toContain(79);
    expect(values).toContain(75);
    expect(values.slice(1)).toEqual([...values.slice(1)].sort((a, b) => (a ?? 0) - (b ?? 0)));
    expect(choices.find((c) => c.value === 30)?.label).toBe("30 giây");
    expect(durationChoices(40, null).find((c) => c.value === 30)?.label).toBe("30 giây (cắt bớt)");
    expect(playsInSlot({ slotS: 60, videoS: 26 })).toBe(2.3);
  });

  it("formats clocks and spans", () => {
    expect(formatClock(65_000)).toBe("1:05");
    expect(formatClock(3_723_000)).toBe("1:02:03");
    expect(formatSpan(90)).toBe("1 phút 30 giây");
    expect(formatSpan(45)).toBe("45 giây");
  });

  it("numbers each product's videos by age", () => {
    const versions = versionNumbers([
      { id: "x2", productId: "p", createdAt: "2026-11-02" },
      { id: "x1", productId: "p", createdAt: "2026-11-01" },
      { id: "y1", productId: "q", createdAt: "2026-11-03" },
    ]);
    expect([versions.get("x1"), versions.get("x2"), versions.get("y1")]).toEqual([1, 2, 1]);
  });
});

describe("player plan", () => {
  const broadcast = adaptBroadcast(
    wireBroadcast([wireItem("a", { duration_s: 50, slot_s: 50 }), wireItem("b")]),
  );
  const tl = buildTimeline(broadcast);
  const byId = itemsById(broadcast);

  it("shows the current slot and preloads the next from its start", () => {
    const pos = locate(
      tl,
      { phase: "body", itemId: "a", index: 0, cycle: 0, offsetMs: 0, atMs: 0, playing: true },
      45_000,
    );
    const plan = planPlayer(tl, byId, pos, true);
    expect(plan.show).toMatchObject({ key: "body:0:0:a", loop: true, timeS: 5 });
    expect(plan.preload).toMatchObject({ key: "body:0:1:b", loop: false, timeS: 0 });
    expect(planPlayer(tl, byId, null, true).show).toBeNull();
  });

  it("gives the same video in the next pass a new key", () => {
    const solo = makeTimeline({ items: [{ id: "b", lengthMs: 20_000 }] });
    const pos = locate(
      solo,
      { phase: "body", itemId: "b", index: 0, cycle: 0, offsetMs: 0, atMs: 0, playing: true },
      19_000,
    );
    const plan = planPlayer(solo, byId, pos, true);
    expect(plan.show?.key).not.toBe(plan.preload?.key);
    expect(plan.preload?.src).toBe(plan.show?.src);
  });

  it("measures drift across the loop seam", () => {
    expect(drift(19.9, 0.1, 20)).toBeCloseTo(0.2);
    expect(drift(19.9, 0.1, null)).toBeCloseTo(19.8);
  });
});

describe("local preview control", () => {
  const tl = makeTimeline({
    items: [
      { id: "a", lengthMs: 10_000 },
      { id: "b", lengthMs: 10_000 },
    ],
  });

  it("starts, pauses, skips and runs off the end like the server", () => {
    const started = applyLocal("start", tl, null, 0)!;
    expect(started).toMatchObject({ itemId: "a", playing: true });
    const paused = applyLocal("pause", tl, started, 4_000)!;
    expect(paused).toMatchObject({ itemId: "a", offsetMs: 4_000, playing: false });
    expect(applyLocal("next", tl, paused, 9_000)).toMatchObject({ itemId: "b", playing: false });
    const counted = { ...tl, loopMode: "count" as const, loopCount: 1 };
    const onB = applyLocal("jump", counted, started, 0, "b")!;
    expect(applyLocal("next", counted, onB, 1_000)).toBeNull();
  });
});
