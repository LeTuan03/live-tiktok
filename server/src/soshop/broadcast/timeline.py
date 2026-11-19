"""Where a broadcast is at any instant: derived from an anchor, never ticked.

A broadcast plays an optional intro once, then its product videos in order,
repeated forever (``continuous``) or ``loop_count`` times (``count``), then an
optional outro once. Each entry is a :class:`Slot` with a length on air.

Nothing advances playback on a timer. A :class:`Playhead` records where
playback stood at one wall-clock instant (``at_ms``) and whether it was
running; :func:`locate` walks forward from it through the slot lengths to the
position at any later instant. Every control action builds a fresh playhead at
"now" from the located position, and so must every playlist edit
(:func:`reanchor` against the timeline as it was before the edit): a walk from
an old anchor through a changed list would land somewhere else.

Times are integer milliseconds so the web player, which runs the same
algorithm in ``web/lib/broadcast/timeline.ts``, agrees with the server to the
millisecond. Keep the two in step. No I/O here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

Phase = Literal["intro", "body", "outro"]
LoopMode = Literal["continuous", "count"]

_Cursor = tuple[Phase, int, int, int]
"""``(phase, index, cycle, offset_ms)``: a point in the show, not yet clamped."""


@dataclass(frozen=True)
class Slot:
    """One playable video and how long it stays on air (``length_ms > 0``)."""

    id: str
    length_ms: int


@dataclass(frozen=True)
class Timeline:
    items: tuple[Slot, ...] = ()
    intro: Slot | None = None
    outro: Slot | None = None
    loop_mode: LoopMode = "continuous"
    loop_count: int = 1

    def __post_init__(self) -> None:
        slots = [*self.items, *(s for s in (self.intro, self.outro) if s is not None)]
        if any(slot.length_ms <= 0 for slot in slots):
            raise ValueError("every slot needs a positive length")
        if self.loop_count < 1:
            raise ValueError("loop_count must be at least 1")

    @property
    def cycle_ms(self) -> int:
        """One pass through the product videos."""
        return sum(slot.length_ms for slot in self.items)

    @property
    def total_ms(self) -> int | None:
        """The whole show, or ``None`` when it loops until stopped."""
        if self.loop_mode == "continuous" and self.items:
            return None
        bookends = sum(s.length_ms for s in (self.intro, self.outro) if s is not None)
        cycles = self.loop_count if self.loop_mode == "count" else 0
        return bookends + self.cycle_ms * cycles


class Playhead(BaseModel):
    """Where playback stood at ``at_ms`` (epoch milliseconds, server clock)."""

    phase: Phase
    item_id: str
    index: int = Field(default=0, ge=0)
    """Body index at anchor time: what plays instead if the item is removed."""
    cycle: int = Field(default=0, ge=0)
    offset_ms: int = Field(default=0, ge=0)
    at_ms: int
    playing: bool = True


@dataclass(frozen=True)
class Position:
    phase: Phase
    item_id: str
    index: int
    """Index among the product videos (0 for the intro and the outro)."""
    cycle: int
    """Completed passes through the product videos (0 during the first)."""
    offset_ms: int
    length_ms: int

    @property
    def remaining_ms(self) -> int:
        return max(self.length_ms - self.offset_ms, 0)

    def anchor(self, now_ms: int, *, playing: bool) -> Playhead:
        return Playhead(
            phase=self.phase,
            item_id=self.item_id,
            index=self.index,
            cycle=self.cycle,
            offset_ms=self.offset_ms,
            at_ms=now_ms,
            playing=playing,
        )


# ------------------------------------------------------------------ walking

_Step = Position | tuple[_Cursor, int] | None


def _step_intro(timeline: Timeline, cursor: _Cursor, elapsed: int) -> _Step:
    slot, offset = timeline.intro, cursor[3]
    if slot is None:
        return ("body", 0, 0, 0), elapsed
    if offset + elapsed < slot.length_ms:
        return Position("intro", slot.id, 0, 0, offset + elapsed, slot.length_ms)
    return ("body", 0, 0, 0), elapsed - max(slot.length_ms - offset, 0)


def _step_body(timeline: Timeline, cursor: _Cursor, elapsed: int) -> _Step:
    _, index, cycle, offset = cursor
    items = timeline.items
    if not items:
        return ("outro", 0, cycle, 0), elapsed
    if index >= len(items):
        # Wrap to the next pass, skipping whole passes at once so a show that
        # has looped for hours costs no more to locate than a fresh one. The
        # pass count is only checked here, so lowering loop_count mid-show
        # lets the current pass finish before the outro.
        cycle += 1
        whole = elapsed // timeline.cycle_ms
        if timeline.loop_mode == "count":
            whole = min(whole, max(timeline.loop_count - cycle, 0))
        cycle, elapsed = cycle + whole, elapsed - whole * timeline.cycle_ms
        if timeline.loop_mode == "count" and cycle >= timeline.loop_count:
            return ("outro", 0, cycle, 0), elapsed
        return ("body", 0, cycle, 0), elapsed
    slot = items[index]
    if offset + elapsed < slot.length_ms:
        return Position("body", slot.id, index, cycle, offset + elapsed, slot.length_ms)
    return ("body", index + 1, cycle, 0), elapsed - max(slot.length_ms - offset, 0)


def _step_outro(timeline: Timeline, cursor: _Cursor, elapsed: int) -> _Step:
    slot, cycle, offset = timeline.outro, cursor[2], cursor[3]
    if slot is not None and offset + elapsed < slot.length_ms:
        return Position("outro", slot.id, 0, cycle, offset + elapsed, slot.length_ms)
    return None


_STEPS = {"intro": _step_intro, "body": _step_body, "outro": _step_outro}


def _walk(timeline: Timeline, cursor: _Cursor, elapsed_ms: int) -> Position | None:
    """The position ``elapsed_ms`` after ``cursor``, or ``None`` past the end."""
    elapsed = max(elapsed_ms, 0)
    while True:
        step = _STEPS[cursor[0]](timeline, cursor, elapsed)
        if step is None or isinstance(step, Position):
            return step
        cursor, elapsed = step


def _resolve(timeline: Timeline, head: Playhead) -> _Cursor | None:
    """The playhead's cursor in ``timeline``, which may have changed since."""
    if head.phase == "intro":
        if timeline.intro is not None and timeline.intro.id == head.item_id:
            return ("intro", 0, 0, head.offset_ms)
        return ("body", 0, 0, 0)
    if head.phase == "outro":
        if timeline.outro is not None and timeline.outro.id == head.item_id:
            return ("outro", 0, head.cycle, head.offset_ms)
        return None
    for index, slot in enumerate(timeline.items):
        if slot.id == head.item_id:
            return ("body", index, head.cycle, head.offset_ms)
    # The item is gone: whatever took its place plays from its start. An
    # index past the end wraps to the next pass.
    return ("body", head.index, head.cycle, 0)


# ------------------------------------------------------------------- public


def locate(timeline: Timeline, head: Playhead, now_ms: int) -> Position | None:
    """Where the show is at ``now_ms``, or ``None`` once it has ended."""
    cursor = _resolve(timeline, head)
    if cursor is None:
        return None
    return _walk(timeline, cursor, now_ms - head.at_ms if head.playing else 0)


def reanchor(timeline: Timeline, head: Playhead, now_ms: int) -> Playhead | None:
    """The same playback, anchored at ``now_ms``. Call it before editing the list."""
    position = locate(timeline, head, now_ms)
    return position.anchor(now_ms, playing=head.playing) if position else None


def _slot_start(timeline: Timeline, phase: Phase, index: int, cycle: int) -> Position | None:
    return _walk(timeline, (phase, index, cycle, 0), 0)


def first(timeline: Timeline) -> Position | None:
    """Where a fresh start begins: the intro, else the first product video."""
    if not timeline.items:
        return None
    return _slot_start(timeline, "intro", 0, 0)


def following(timeline: Timeline, position: Position) -> Position | None:
    """The start of the slot after ``position``, or ``None`` if the show ends there."""
    return _walk(timeline, (position.phase, position.index, position.cycle, position.length_ms), 0)


def preceding(timeline: Timeline, position: Position) -> Position | None:
    """The start of the slot before ``position``. The very first slot restarts."""
    last = len(timeline.items) - 1
    if position.phase == "outro":
        if last >= 0:
            return _slot_start(timeline, "body", last, max(position.cycle - 1, 0))
        return _slot_start(timeline, "outro", 0, position.cycle)
    if position.phase == "body":
        if position.index > 0:
            return _slot_start(timeline, "body", position.index - 1, position.cycle)
        if position.cycle > 0:
            return _slot_start(timeline, "body", last, position.cycle - 1)
    return _slot_start(timeline, "intro", 0, 0)


def jump_to(timeline: Timeline, item_id: str, current: Position | None) -> Position | None:
    """The start of slot ``item_id``, keeping the current pass. ``None`` if unknown."""
    cycle = 0
    if current is not None:
        cycle = max(current.cycle - 1, 0) if current.phase == "outro" else current.cycle
    if timeline.loop_mode == "count":
        cycle = min(cycle, timeline.loop_count - 1)
    if timeline.intro is not None and timeline.intro.id == item_id:
        return _slot_start(timeline, "intro", 0, 0)
    if timeline.outro is not None and timeline.outro.id == item_id:
        return _slot_start(timeline, "outro", 0, cycle + 1)
    for index, slot in enumerate(timeline.items):
        if slot.id == item_id:
            return _slot_start(timeline, "body", index, cycle)
    return None


def wind_down(timeline: Timeline, position: Position) -> Position | None:
    """Where "end with the outro" goes: the outro from its start, else nowhere."""
    if timeline.outro is None:
        return None
    if position.phase == "outro":
        return position
    cycle = position.cycle + (1 if position.phase == "body" else 0)
    return _slot_start(timeline, "outro", 0, cycle)
