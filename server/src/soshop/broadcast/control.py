"""What a control-room action does to a broadcast's playback (pure).

:func:`plan` maps an action, the current timeline and the stored playhead to
the new status and playhead. The service layer runs it under a row lock and
stores the result; nothing here touches the database or the clock.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from soshop.broadcast.timeline import (
    Playhead,
    Position,
    Timeline,
    first,
    following,
    jump_to,
    locate,
    preceding,
    wind_down,
)

Status = Literal["idle", "live", "paused", "ended"]
Action = Literal["start", "pause", "resume", "next", "prev", "jump", "finish", "stop"]

ON_AIR: tuple[Status, ...] = ("live", "paused")


class ControlError(ValueError):
    """The action does not apply to the broadcast as it stands."""


@dataclass(frozen=True)
class Outcome:
    status: Status
    playhead: Playhead | None
    started: bool = False
    """True when the show starts over (the start time is reset)."""


_NOT_IN_LIST = "Video này không có trong danh sách phát."


def _jump(timeline: Timeline, current: Position, item_id: str | None) -> Position | None:
    target = jump_to(timeline, item_id or "", current)
    if target is None:
        raise ControlError(_NOT_IN_LIST)
    return target


_MOVES: dict[str, Callable[[Timeline, Position, str | None], Position | None]] = {
    "pause": lambda _t, current, _i: current,
    "resume": lambda _t, current, _i: current,
    "next": lambda timeline, current, _i: following(timeline, current),
    "prev": lambda timeline, current, _i: preceding(timeline, current),
    "jump": _jump,
    "finish": lambda timeline, current, _i: wind_down(timeline, current),
}
_PLAYING = {"pause": False, "resume": True, "finish": True}


def _start(timeline: Timeline, now_ms: int, item_id: str | None) -> Outcome:
    if not timeline.items:
        raise ControlError("Phiên chưa có video sản phẩm nào phát được.")
    target = jump_to(timeline, item_id, None) if item_id else first(timeline)
    if target is None:
        raise ControlError(_NOT_IN_LIST)
    return Outcome("live", target.anchor(now_ms, playing=True), started=True)


def plan(
    action: Action,
    timeline: Timeline,
    status: Status,
    head: Playhead | None,
    now_ms: int,
    item_id: str | None = None,
) -> Outcome:
    """The status and playhead ``action`` leads to at ``now_ms``.

    ``start`` always starts over (from ``item_id`` when given). ``finish``
    plays the outro and then ends, or ends at once without one. Running off
    the end of the show, by time or by ``next``, ends it.
    """
    if action == "start":
        return _start(timeline, now_ms, item_id)
    if action == "stop":
        return Outcome("ended", None)
    if status not in ON_AIR or head is None:
        raise ControlError("Phiên livestream chưa bắt đầu.")
    current = locate(timeline, head, now_ms)
    if current is None:
        return Outcome("ended", None)
    target = _MOVES[action](timeline, current, item_id)
    if target is None:
        return Outcome("ended", None)
    playing = _PLAYING.get(action, head.playing)
    return Outcome("live" if playing else "paused", target.anchor(now_ms, playing=playing))
