// Where a broadcast is at any instant, derived from an anchor and never ticked.
//
// TypeScript port of `server/src/soshop/broadcast/timeline.py`: the same
// algorithm on the same integer milliseconds, so the player and the server
// agree on what is on air. Keep the two in step; `timeline.test.ts` mirrors
// the Python tests.
//
// A show plays an optional intro once, then the product videos in order,
// repeated forever ("continuous") or `loopCount` times ("count"), then an
// optional outro once. A `Playhead` records where playback stood at `atMs`
// (server clock); `locate` walks forward through the slot lengths from it.

export type Phase = "intro" | "body" | "outro";
export type LoopMode = "continuous" | "count";

export interface Slot {
  id: string;
  /** Time on air, > 0. */
  lengthMs: number;
}

export interface Timeline {
  items: Slot[];
  intro: Slot | null;
  outro: Slot | null;
  loopMode: LoopMode;
  loopCount: number;
}

export interface Playhead {
  phase: Phase;
  itemId: string;
  /** Body index at anchor time: what plays instead if the item is removed. */
  index: number;
  cycle: number;
  offsetMs: number;
  /** Epoch milliseconds on the server clock. */
  atMs: number;
  playing: boolean;
}

export interface Position {
  phase: Phase;
  itemId: string;
  /** Index among the product videos (0 for the intro and the outro). */
  index: number;
  /** Completed passes through the product videos (0 during the first). */
  cycle: number;
  offsetMs: number;
  lengthMs: number;
}

type Cursor = [phase: Phase, index: number, cycle: number, offsetMs: number];
type Step = Position | [Cursor, number] | null;

export function makeTimeline(parts: Partial<Timeline> = {}): Timeline {
  return {
    items: parts.items ?? [],
    intro: parts.intro ?? null,
    outro: parts.outro ?? null,
    loopMode: parts.loopMode ?? "continuous",
    loopCount: Math.max(parts.loopCount ?? 1, 1),
  };
}

/** One pass through the product videos. */
export function cycleMs(timeline: Timeline): number {
  return timeline.items.reduce((sum, slot) => sum + slot.lengthMs, 0);
}

/** The whole show, or `null` when it loops until stopped. */
export function totalMs(timeline: Timeline): number | null {
  if (timeline.loopMode === "continuous" && timeline.items.length > 0) return null;
  const bookends = (timeline.intro?.lengthMs ?? 0) + (timeline.outro?.lengthMs ?? 0);
  const cycles = timeline.loopMode === "count" ? timeline.loopCount : 0;
  return bookends + cycleMs(timeline) * cycles;
}

export function remainingMs(position: Position): number {
  return Math.max(position.lengthMs - position.offsetMs, 0);
}

function stepIntro(timeline: Timeline, cursor: Cursor, elapsed: number): Step {
  const slot = timeline.intro;
  const offset = cursor[3];
  if (!slot) return [["body", 0, 0, 0], elapsed];
  if (offset + elapsed < slot.lengthMs) {
    return {
      phase: "intro",
      itemId: slot.id,
      index: 0,
      cycle: 0,
      offsetMs: offset + elapsed,
      lengthMs: slot.lengthMs,
    };
  }
  return [["body", 0, 0, 0], elapsed - Math.max(slot.lengthMs - offset, 0)];
}

function stepBody(timeline: Timeline, cursor: Cursor, elapsed: number): Step {
  const [, index, cycle, offset] = cursor;
  const items = timeline.items;
  if (items.length === 0) return [["outro", 0, cycle, 0], elapsed];
  if (index >= items.length) {
    // Wrap to the next pass, skipping whole passes at once. The pass count is
    // only checked here, so lowering loopCount mid-show lets the pass finish.
    let next = cycle + 1;
    const total = cycleMs(timeline);
    let whole = Math.floor(elapsed / total);
    if (timeline.loopMode === "count")
      whole = Math.min(whole, Math.max(timeline.loopCount - next, 0));
    next += whole;
    const left = elapsed - whole * total;
    if (timeline.loopMode === "count" && next >= timeline.loopCount) {
      return [["outro", 0, next, 0], left];
    }
    return [["body", 0, next, 0], left];
  }
  const slot = items[index];
  if (offset + elapsed < slot.lengthMs) {
    return {
      phase: "body",
      itemId: slot.id,
      index,
      cycle,
      offsetMs: offset + elapsed,
      lengthMs: slot.lengthMs,
    };
  }
  return [["body", index + 1, cycle, 0], elapsed - Math.max(slot.lengthMs - offset, 0)];
}

function stepOutro(timeline: Timeline, cursor: Cursor, elapsed: number): Step {
  const slot = timeline.outro;
  const [, , cycle, offset] = cursor;
  if (slot && offset + elapsed < slot.lengthMs) {
    return {
      phase: "outro",
      itemId: slot.id,
      index: 0,
      cycle,
      offsetMs: offset + elapsed,
      lengthMs: slot.lengthMs,
    };
  }
  return null;
}

const STEPS: Record<Phase, (timeline: Timeline, cursor: Cursor, elapsed: number) => Step> = {
  intro: stepIntro,
  body: stepBody,
  outro: stepOutro,
};

function walk(timeline: Timeline, start: Cursor, elapsedMs: number): Position | null {
  let cursor = start;
  let elapsed = Math.max(Math.round(elapsedMs), 0);
  for (;;) {
    const step = STEPS[cursor[0]](timeline, cursor, elapsed);
    if (step === null || !Array.isArray(step)) return step;
    [cursor, elapsed] = step;
  }
}

function resolve(timeline: Timeline, head: Playhead): Cursor | null {
  if (head.phase === "intro") {
    if (timeline.intro?.id === head.itemId) return ["intro", 0, 0, head.offsetMs];
    return ["body", 0, 0, 0];
  }
  if (head.phase === "outro") {
    if (timeline.outro?.id === head.itemId) return ["outro", 0, head.cycle, head.offsetMs];
    return null;
  }
  const index = timeline.items.findIndex((slot) => slot.id === head.itemId);
  if (index >= 0) return ["body", index, head.cycle, head.offsetMs];
  // The item is gone: whatever took its place plays from its start.
  return ["body", head.index, head.cycle, 0];
}

/** Where the show is at `nowMs` (server clock), or `null` once it has ended. */
export function locate(timeline: Timeline, head: Playhead, nowMs: number): Position | null {
  const cursor = resolve(timeline, head);
  if (cursor === null) return null;
  return walk(timeline, cursor, head.playing ? nowMs - head.atMs : 0);
}

export function anchor(position: Position, nowMs: number, playing: boolean): Playhead {
  return {
    phase: position.phase,
    itemId: position.itemId,
    index: position.index,
    cycle: position.cycle,
    offsetMs: position.offsetMs,
    atMs: Math.round(nowMs),
    playing,
  };
}

/** The same playback anchored at `nowMs`; call it before editing the list. */
export function reanchor(timeline: Timeline, head: Playhead, nowMs: number): Playhead | null {
  const position = locate(timeline, head, nowMs);
  return position ? anchor(position, nowMs, head.playing) : null;
}

function slotStart(timeline: Timeline, phase: Phase, index: number, cycle: number) {
  return walk(timeline, [phase, index, cycle, 0], 0);
}

/** Where a fresh start begins: the intro, else the first product video. */
export function first(timeline: Timeline): Position | null {
  if (timeline.items.length === 0) return null;
  return slotStart(timeline, "intro", 0, 0);
}

/** The start of the slot after `position`, or `null` if the show ends there. */
export function following(timeline: Timeline, position: Position): Position | null {
  return walk(timeline, [position.phase, position.index, position.cycle, position.lengthMs], 0);
}

/** The start of the slot before `position`. The very first slot restarts. */
export function preceding(timeline: Timeline, position: Position): Position | null {
  const last = timeline.items.length - 1;
  if (position.phase === "outro") {
    if (last >= 0) return slotStart(timeline, "body", last, Math.max(position.cycle - 1, 0));
    return slotStart(timeline, "outro", 0, position.cycle);
  }
  if (position.phase === "body") {
    if (position.index > 0) return slotStart(timeline, "body", position.index - 1, position.cycle);
    if (position.cycle > 0) return slotStart(timeline, "body", last, position.cycle - 1);
  }
  return slotStart(timeline, "intro", 0, 0);
}

/** The start of slot `itemId`, keeping the current pass; `null` if unknown. */
export function jumpTo(
  timeline: Timeline,
  itemId: string,
  current: Position | null,
): Position | null {
  let cycle = 0;
  if (current) cycle = current.phase === "outro" ? Math.max(current.cycle - 1, 0) : current.cycle;
  if (timeline.loopMode === "count") cycle = Math.min(cycle, timeline.loopCount - 1);
  if (timeline.intro?.id === itemId) return slotStart(timeline, "intro", 0, 0);
  if (timeline.outro?.id === itemId) return slotStart(timeline, "outro", 0, cycle + 1);
  const index = timeline.items.findIndex((slot) => slot.id === itemId);
  return index >= 0 ? slotStart(timeline, "body", index, cycle) : null;
}

/** Where "end with the outro" goes: the outro from its start, else nowhere. */
export function windDown(timeline: Timeline, position: Position): Position | null {
  if (!timeline.outro) return null;
  if (position.phase === "outro") return position;
  return slotStart(timeline, "outro", 0, position.cycle + (position.phase === "body" ? 1 : 0));
}

/** Identifies one slot instance: the same video in the next pass is a new one. */
export function slotKey(position: Position): string {
  return `${position.phase}:${position.cycle}:${position.index}:${position.itemId}`;
}
