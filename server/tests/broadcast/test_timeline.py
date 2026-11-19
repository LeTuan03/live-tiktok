"""The derived broadcast timeline: locate, moves, and re-anchoring around edits.

``web/lib/broadcast/timeline.test.ts`` runs the same cases against the
TypeScript port; keep the two in step.
"""

from __future__ import annotations

import pytest

from soshop.broadcast.timeline import (
    Playhead,
    Position,
    Slot,
    Timeline,
    first,
    following,
    jump_to,
    locate,
    preceding,
    reanchor,
    wind_down,
)

A, B, C = Slot("a", 10_000), Slot("b", 20_000), Slot("c", 5_000)
INTRO, OUTRO = Slot("intro", 5_000), Slot("outro", 4_000)


def head(item: str, *, phase: str = "body", index: int = 0, cycle: int = 0, offset: int = 0,
         at: int = 0, playing: bool = True) -> Playhead:  # fmt: skip
    return Playhead.model_validate(
        {
            "phase": phase,
            "item_id": item,
            "index": index,
            "cycle": cycle,
            "offset_ms": offset,
            "at_ms": at,
            "playing": playing,
        }
    )


def where(position: Position | None) -> tuple[str, str, int, int] | None:
    if position is None:
        return None
    return position.phase, position.item_id, position.cycle, position.offset_ms


def test_continuous_loops_forever_and_skips_whole_cycles() -> None:
    tl = Timeline(items=(A, B))
    start = head("a")
    assert where(locate(tl, start, 5_000)) == ("body", "a", 0, 5_000)
    assert where(locate(tl, start, 10_000)) == ("body", "b", 0, 0)
    assert where(locate(tl, start, 29_999)) == ("body", "b", 0, 19_999)
    assert where(locate(tl, start, 30_000)) == ("body", "a", 1, 0)
    # An hour in: 120 whole passes plus five seconds.
    assert where(locate(tl, start, 3_605_000)) == ("body", "a", 120, 5_000)
    assert tl.cycle_ms == 30_000 and tl.total_ms is None


def test_count_mode_plays_intro_passes_outro_then_ends() -> None:
    tl = Timeline(items=(A, B), intro=INTRO, outro=OUTRO, loop_mode="count", loop_count=2)
    begin = first(tl)
    assert begin is not None and begin.phase == "intro"
    start = begin.anchor(0, playing=True)
    assert where(locate(tl, start, 5_000)) == ("body", "a", 0, 0)
    assert where(locate(tl, start, 35_000)) == ("body", "a", 1, 0)
    assert where(locate(tl, start, 65_000)) == ("outro", "outro", 2, 0)
    assert locate(tl, start, 69_000) is None
    assert locate(tl, start, 10_000_000) is None
    assert tl.total_ms == 69_000


def test_paused_playhead_stays_put() -> None:
    tl = Timeline(items=(A, B))
    paused = head("b", index=1, offset=7_000, playing=False)
    assert where(locate(tl, paused, 999_999)) == ("body", "b", 0, 7_000)


def test_following_and_preceding() -> None:
    tl = Timeline(items=(A, B), intro=INTRO, outro=OUTRO, loop_mode="count", loop_count=2)
    on_a = locate(tl, head("a"), 1_000)
    on_b = locate(tl, head("b", index=1), 1_000)
    on_b_last = locate(tl, head("b", index=1, cycle=1), 1_000)
    on_intro = locate(tl, head("intro", phase="intro"), 1_000)
    on_outro = locate(tl, head("outro", phase="outro", cycle=2), 1_000)
    assert on_a and on_b and on_b_last and on_intro and on_outro
    assert where(following(tl, on_intro)) == ("body", "a", 0, 0)
    assert where(following(tl, on_a)) == ("body", "b", 0, 0)
    assert where(following(tl, on_b)) == ("body", "a", 1, 0)
    assert where(following(tl, on_b_last)) == ("outro", "outro", 2, 0)
    assert following(tl, on_outro) is None
    assert where(preceding(tl, on_b)) == ("body", "a", 0, 0)
    assert where(preceding(tl, on_a)) == ("intro", "intro", 0, 0)
    assert where(preceding(tl, on_intro)) == ("intro", "intro", 0, 0)
    assert where(preceding(tl, on_outro)) == ("body", "b", 1, 0)
    on_a_second = locate(tl, head("a", cycle=1), 1_000)
    assert on_a_second and where(preceding(tl, on_a_second)) == ("body", "b", 0, 0)


def test_preceding_without_intro_restarts_the_first_video() -> None:
    tl = Timeline(items=(A, B))
    on_a = locate(tl, head("a", offset=4_000), 0)
    assert on_a and where(preceding(tl, on_a)) == ("body", "a", 0, 0)


def test_jump_keeps_the_pass_and_clamps_it_in_count_mode() -> None:
    tl = Timeline(items=(A, B), outro=OUTRO, loop_mode="count", loop_count=2)
    on_a = locate(tl, head("a", cycle=1), 0)
    on_outro = locate(tl, head("outro", phase="outro", cycle=2), 0)
    assert on_a and on_outro
    assert where(jump_to(tl, "b", on_a)) == ("body", "b", 1, 0)
    assert where(jump_to(tl, "a", on_outro)) == ("body", "a", 1, 0)
    assert where(jump_to(tl, "outro", on_a)) == ("outro", "outro", 2, 0)
    assert where(jump_to(tl, "b", None)) == ("body", "b", 0, 0)
    assert jump_to(tl, "missing", on_a) is None


def test_wind_down_goes_to_the_outro_or_ends() -> None:
    with_outro = Timeline(items=(A, B), outro=OUTRO)
    on_b = locate(with_outro, head("b", index=1, cycle=3), 0)
    assert on_b and where(wind_down(with_outro, on_b)) == ("outro", "outro", 4, 0)
    on_outro = locate(with_outro, head("outro", phase="outro", offset=1_000), 0)
    assert on_outro and wind_down(with_outro, on_outro) == on_outro
    plain = Timeline(items=(A, B))
    on_a = locate(plain, head("a"), 0)
    assert on_a and wind_down(plain, on_a) is None


def test_reanchor_keeps_the_current_video_through_an_edit() -> None:
    before = Timeline(items=(A, B, C))
    anchored = reanchor(before, head("a"), 12_000)  # 2 s into B
    assert anchored is not None
    assert (anchored.item_id, anchored.offset_ms, anchored.at_ms) == ("b", 2_000, 12_000)
    after = Timeline(items=(C, B))  # A removed, C moved first
    assert where(locate(after, anchored, 13_000)) == ("body", "b", 0, 3_000)
    # Walking the stale anchor through the new list lands somewhere else.
    assert where(locate(after, head("a"), 13_000)) != ("body", "b", 0, 3_000)


def test_removed_current_video_hands_over_to_its_successor() -> None:
    playing_b = head("b", index=1, offset=5_000, at=0)
    assert where(locate(Timeline(items=(A, C)), playing_b, 1_000)) == ("body", "c", 0, 1_000)
    # B was last: the next pass begins.
    assert where(locate(Timeline(items=(A,)), playing_b, 1_000)) == ("body", "a", 1, 1_000)


def test_removed_bookends() -> None:
    in_intro = head("intro", phase="intro", offset=1_000)
    assert where(locate(Timeline(items=(A,)), in_intro, 0)) == ("body", "a", 0, 0)
    in_outro = head("outro", phase="outro")
    assert locate(Timeline(items=(A,)), in_outro, 0) is None


def test_shortened_slot_moves_on_without_borrowing_time() -> None:
    tl = Timeline(items=(Slot("a", 5_000), B))
    past_end = head("a", offset=8_000)
    assert where(locate(tl, past_end, 1_000)) == ("body", "b", 0, 1_000)


def test_lowering_loop_count_finishes_the_current_pass() -> None:
    tl = Timeline(items=(A, B), outro=OUTRO, loop_mode="count", loop_count=2)
    late = head("a", cycle=3)
    assert where(locate(tl, late, 15_000)) == ("body", "b", 3, 5_000)
    assert where(locate(tl, late, 30_000)) == ("outro", "outro", 4, 0)


def test_empty_list_cannot_start() -> None:
    tl = Timeline(intro=INTRO)
    assert first(tl) is None
    assert locate(tl, head("a"), 0) is None


@pytest.mark.parametrize(
    "kwargs",
    [{"items": (Slot("a", 0),)}, {"intro": Slot("i", -1)}, {"items": (A,), "loop_count": 0}],
)
def test_timeline_rejects_bad_slots(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        Timeline(**kwargs)  # type: ignore[arg-type]
