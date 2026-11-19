// Pure helpers for the livestream session UI: the playable timeline of a
// broadcast, the play-order preview, time-on-air choices and labels.

import {
  cycleMs,
  following,
  locate,
  makeTimeline,
  type Playhead,
  type Position,
  type Slot,
  type Timeline,
} from "./timeline";
import type { Broadcast, BroadcastItem, BroadcastStatus } from "./types";

export const STATUS_LABEL: Record<BroadcastStatus, string> = {
  idle: "Chưa phát",
  live: "Đang phát",
  paused: "Tạm dừng",
  ended: "Đã kết thúc",
};

export const STATUS_TONE: Record<BroadcastStatus, "neutral" | "live" | "amber" | "danger"> = {
  idle: "neutral",
  live: "live",
  paused: "amber",
  ended: "neutral",
};

export function isOnAir(status: BroadcastStatus): boolean {
  return status === "live" || status === "paused";
}

function slotOf(item: BroadcastItem | null): Slot | null {
  if (!item?.playable || !item.slotS) return null;
  return { id: item.id, lengthMs: Math.max(Math.round(item.slotS * 1000), 1) };
}

/** The playable part of a broadcast, exactly as the server builds it. */
export function buildTimeline(broadcast: Broadcast): Timeline {
  return makeTimeline({
    items: broadcast.items.map(slotOf).filter((slot): slot is Slot => slot !== null),
    intro: slotOf(broadcast.intro),
    outro: slotOf(broadcast.outro),
    loopMode: broadcast.loopMode,
    loopCount: broadcast.loopCount,
  });
}

export function allItems(broadcast: Broadcast): BroadcastItem[] {
  return [broadcast.intro, ...broadcast.items, broadcast.outro].filter(
    (item): item is BroadcastItem => item !== null,
  );
}

export function itemsById(broadcast: Broadcast): Map<string, BroadcastItem> {
  return new Map(allItems(broadcast).map((item) => [item.id, item]));
}

/** Where the show is now, on this browser's clock corrected to the server's. */
export function positionAt(
  timeline: Timeline,
  playhead: Playhead | null,
  clockOffsetMs: number,
  browserNowMs: number,
): Position | null {
  return playhead ? locate(timeline, playhead, browserNowMs + clockOffsetMs) : null;
}

export function nextItem(
  timeline: Timeline,
  position: Position | null,
  byId: Map<string, BroadcastItem>,
): BroadcastItem | null {
  if (!position) return null;
  const next = following(timeline, position);
  return next ? (byId.get(next.itemId) ?? null) : null;
}

export interface ScheduleRow {
  item: BroadcastItem;
  /** Start within the first pass, or `null` when the item is skipped. */
  startMs: number | null;
  lengthMs: number | null;
}

/** Play order of one pass (intro, product videos, outro) with start times. */
export function schedule(broadcast: Broadcast): ScheduleRow[] {
  const rows: ScheduleRow[] = [];
  let at = 0;
  for (const item of allItems(broadcast)) {
    const slot = slotOf(item);
    rows.push({ item, startMs: slot ? at : null, lengthMs: slot?.lengthMs ?? null });
    if (slot) at += slot.lengthMs;
  }
  return rows;
}

export function cycleSeconds(timeline: Timeline): number {
  return cycleMs(timeline) / 1000;
}

/** "1:05", "12:00", "1:02:03". */
export function formatClock(ms: number): string {
  const total = Math.max(Math.floor(ms / 1000), 0);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}

/** "45 giây", "1 phút 30 giây", "2 giờ 5 phút". */
export function formatSpan(seconds: number): string {
  const total = Math.round(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return m > 0 ? `${h} giờ ${m} phút` : `${h} giờ`;
  if (m > 0) return s > 0 ? `${m} phút ${s} giây` : `${m} phút`;
  return `${s} giây`;
}

export interface DurationChoice {
  /** Seconds on air; `null` plays the video once. */
  value: number | null;
  label: string;
}

const PRESETS = [30, 45, 60, 90, 120, 180, 300];

/** Time-on-air options for one item: whole repeats of its video plus round presets. */
export function durationChoices(videoS: number | null, current: number | null): DurationChoice[] {
  const seen = new Set<number>();
  const choices: DurationChoice[] = [];
  const video = videoS && videoS > 0 ? videoS : null;
  choices.push({ value: null, label: video ? `1 lần (${Math.round(video)} giây)` : "Theo video" });
  if (video) seen.add(Math.round(video));
  const add = (value: number, label: string) => {
    if (value < 5 || value > 3600 || seen.has(value)) return;
    seen.add(value);
    choices.push({ value, label });
  };
  if (video) {
    for (const times of [2, 3]) {
      const value = Math.ceil(video * times);
      add(value, `${times} lần (${value} giây)`);
    }
  }
  for (const value of PRESETS)
    add(value, `${formatSpan(value)}${video && value < video ? " (cắt bớt)" : ""}`);
  if (current !== null) add(current, formatSpan(current));
  const [once, ...rest] = choices;
  return [once, ...rest.sort((a, b) => (a.value ?? 0) - (b.value ?? 0))];
}

/** How often the video repeats in its slot, e.g. 2.3 for 60 s of a 26 s video. */
export function playsInSlot(item: Pick<BroadcastItem, "slotS" | "videoS">): number | null {
  if (!item.slotS || !item.videoS) return null;
  return Math.round((item.slotS / item.videoS) * 10) / 10;
}

/** Version numbers ("Bản 1, 2, ...") by creation order within each product. */
export function versionNumbers<T extends { id: string; productId: string; createdAt: string }>(
  videos: T[],
): Map<string, number> {
  const byProduct = new Map<string, T[]>();
  for (const video of videos) {
    byProduct.set(video.productId, [...(byProduct.get(video.productId) ?? []), video]);
  }
  const out = new Map<string, number>();
  for (const list of byProduct.values()) {
    const sorted = [...list].sort((a, b) => a.createdAt.localeCompare(b.createdAt));
    for (const [i, video] of sorted.entries()) out.set(video.id, i + 1);
  }
  return out;
}

/** "Vòng 3" or "Vòng 2/3". */
export function cycleLabel(position: Position | null, broadcast: Broadcast): string {
  if (position?.phase !== "body") return "";
  const n = position.cycle + 1;
  return broadcast.loopMode === "count" ? `Vòng ${n}/${broadcast.loopCount}` : `Vòng ${n}`;
}
