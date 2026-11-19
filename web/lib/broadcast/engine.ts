// What the two-layer video player should show and preload at one instant.
//
// The player keeps two <video> elements: the visible one plays the current
// slot while the hidden one loads the next, so a transition is a swap of
// already-buffered elements, not a fresh load. `planPlayer` is the pure part
// (tested); `BroadcastPlayer` applies the plan to the DOM.

import { following, type Position, slotKey, type Timeline } from "./timeline";
import type { BroadcastItem } from "./types";

export interface LayerPlan {
  /** One slot instance; the same video in the next pass gets a new key. */
  key: string;
  src: string;
  /** True when the slot is longer than the video, which then repeats. */
  loop: boolean;
  /** Where in the video playback should be now, in seconds. */
  timeS: number;
  videoS: number;
}

export interface PlayerPlan {
  show: LayerPlan | null;
  preload: LayerPlan | null;
  playing: boolean;
}

export const IDLE_PLAN: PlayerPlan = { show: null, preload: null, playing: false };

/** Re-seek a playing video only when it is further than this from the schedule. */
export const DRIFT_TOLERANCE_S = 1.0;

export function layerFor(position: Position, item: BroadcastItem): LayerPlan | null {
  if (!item.videoUrl || !item.videoS) return null;
  const videoMs = Math.max(Math.round(item.videoS * 1000), 1);
  return {
    key: slotKey(position),
    src: item.videoUrl,
    loop: position.lengthMs > videoMs + 50,
    timeS: (position.offsetMs % videoMs) / 1000,
    videoS: item.videoS,
  };
}

export function planPlayer(
  timeline: Timeline,
  byId: Map<string, BroadcastItem>,
  position: Position | null,
  playing: boolean,
): PlayerPlan {
  if (!position) return IDLE_PLAN;
  const item = byId.get(position.itemId);
  const show = item ? layerFor(position, item) : null;
  if (!show) return IDLE_PLAN;
  const next = following(timeline, position);
  const nextItem = next ? byId.get(next.itemId) : undefined;
  const preload = next && nextItem ? layerFor({ ...next, offsetMs: 0 }, nextItem) : null;
  return { show, preload, playing };
}

/** Distance between two video times; across the loop seam when the video repeats. */
export function drift(actualS: number, expectedS: number, loopS: number | null): number {
  const d = Math.abs(actualS - expectedS);
  return loopS ? Math.min(d, Math.abs(loopS - d)) : d;
}
