// Control actions applied to a local playhead, for the "Xem thử" preview.
// Mirrors `server/src/soshop/broadcast/control.py::plan`; on air the server
// runs the real thing and the page only displays its result.

import {
  anchor,
  first,
  following,
  jumpTo,
  locate,
  type Playhead,
  type Position,
  preceding,
  type Timeline,
  windDown,
} from "./timeline";

export type LocalAction = "start" | "pause" | "resume" | "next" | "prev" | "jump" | "finish";

/** The new local playhead, or `null` when the action ends the show. */
export function applyLocal(
  action: LocalAction,
  timeline: Timeline,
  head: Playhead | null,
  nowMs: number,
  itemId?: string,
): Playhead | null {
  if (action === "start") {
    const target = itemId ? jumpTo(timeline, itemId, null) : first(timeline);
    return target ? anchor(target, nowMs, true) : null;
  }
  if (!head) return null;
  const current = locate(timeline, head, nowMs);
  if (!current) return null;
  const moves: Record<Exclude<LocalAction, "start">, () => Position | null> = {
    pause: () => current,
    resume: () => current,
    next: () => following(timeline, current),
    prev: () => preceding(timeline, current),
    jump: () => jumpTo(timeline, itemId ?? "", current),
    finish: () => windDown(timeline, current),
  };
  const target = moves[action]();
  if (!target) return null;
  const playing =
    action === "pause" ? false : action === "resume" || action === "finish" ? true : head.playing;
  return anchor(target, nowMs, playing);
}
