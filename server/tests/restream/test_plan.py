"""What the restream feed does as the broadcast moves (pure, no processes)."""

from __future__ import annotations

from soshop.broadcast.timeline import Position, Slot, Timeline
from soshop.restream.plan import (
    AHEAD_MS,
    MAX_LAG_MS,
    RETRY_MS,
    STARTUP_GRACE_MS,
    Cue,
    Failure,
    Feed,
    Show,
    Source,
    Step,
    decide,
)

A, B, C = Slot("a", 10_000), Slot("b", 20_000), Slot("c", 8_000)
TIMELINE = Timeline(items=(A, B, C))
SOURCES = {
    "a": Source("/media/promo/a/video.mp4", 10_000, "Son"),
    "b": Source("/media/promo/b/video.mp4", 8_000, "Kem"),  # 20 s slot, 8 s video
    "c": Source("/media/uploads/c.mp4", 8_000, "Sữa rửa mặt"),
}
NOW = 1_000_000
INDEX = {"a": 0, "b": 1, "c": 2}
LENGTH = {"a": 10_000, "b": 20_000, "c": 8_000}


def pos(item: str, offset: int, *, cycle: int = 0) -> Position:
    return Position("body", item, INDEX[item], cycle, offset, LENGTH[item])


def show(item: str, offset: int, *, playing: bool = True, timeline: Timeline = TIMELINE) -> Show:
    return Show(timeline, pos(item, offset), playing, SOURCES, NOW)


def cue(item: str, offset: int, kind: str = "play") -> Cue:
    return Cue(kind, pos(item, offset), SOURCES[item])  # type: ignore[arg-type]


def feed(
    item: str,
    start: int,
    sent: int,
    *,
    kind: str = "play",
    sending: bool = True,
    encoded: bool = False,
    queued: Cue | None = None,
    started_ms: int = NOW - 1_000,
) -> Feed:
    return Feed(cue(item, start, kind), sent, started_ms, sending, encoded, queued)


def test_cue_knows_where_in_the_video_a_long_slot_is() -> None:
    later = cue("b", 9_000)  # 20 s slot of an 8 s video: second pass, 1 s in
    assert later.seek_ms == 1_000 and later.remaining_ms == 11_000 and later.loops
    whole = cue("a", 4_000)
    assert whole.seek_ms == 4_000 and whole.remaining_ms == 6_000 and not whole.loops


def test_off_air_stops_and_nothing_running_starts_at_the_derived_position() -> None:
    assert decide(None, None) == Step(stop=True)
    assert decide(feed("a", 0, 500), None) == Step(stop=True)
    assert decide(None, show("a", 4_000)) == Step(restart=cue("a", 4_000))


def test_an_unknown_source_stops_the_feed() -> None:
    bare = Show(TIMELINE, pos("a", 0), True, {}, NOW)
    assert decide(None, bare) == Step(stop=True)


def test_a_slot_about_to_end_is_not_worth_a_new_encoder() -> None:
    assert decide(None, show("a", 9_900)) == Step()


def test_in_step_the_feed_is_left_alone() -> None:
    assert decide(feed("a", 0, 4_000), show("a", 4_300)) == Step()
    assert decide(feed("a", 0, 4_000), show("a", 4_000 + MAX_LAG_MS)) == Step()
    assert decide(feed("a", 0, 4_000), show("a", 4_000 - AHEAD_MS)) == Step()


def test_drift_beyond_the_allowance_restarts_at_the_derived_position() -> None:
    assert decide(feed("a", 0, 4_000), show("a", 6_500)) == Step(restart=cue("a", 6_500))
    assert decide(feed("a", 0, 4_000), show("a", 2_000)) == Step(restart=cue("a", 2_000))


def test_the_end_of_a_slot_is_not_cut_when_the_show_just_moved_on() -> None:
    # The feed trails by a moment: still sending the end of "a" as "b" begins.
    trailing = feed("a", 0, 9_700, encoded=True, queued=cue("b", 0))
    assert decide(trailing, show("b", 200)) == Step()


def test_a_control_action_restarts_at_once() -> None:
    # "next" pressed in the middle of "a": the show is at the start of "b".
    assert decide(feed("a", 0, 3_000), show("b", 100)) == Step(restart=cue("b", 100))
    # "jump" to "c" from "a".
    assert decide(feed("a", 0, 3_000), show("c", 0)) == Step(restart=cue("c", 0))


def test_pause_holds_the_frame_and_resume_plays_on() -> None:
    paused = show("a", 4_200, playing=False)
    assert decide(feed("a", 0, 4_100), paused) == Step(restart=cue("a", 4_200, "hold"))
    holding = feed("a", 4_200, 4_200, kind="hold")
    assert decide(holding, paused) == Step()
    # Moved while paused ("prev"): hold the new frame.
    assert decide(holding, show("a", 0, playing=False)) == Step(restart=cue("a", 0, "hold"))
    assert decide(holding, show("a", 4_200)) == Step(restart=cue("a", 4_200))


def test_the_next_slot_is_queued_once_the_current_one_is_encoded() -> None:
    assert decide(feed("a", 0, 6_000), show("a", 6_100)) == Step()
    done = feed("a", 0, 6_000, encoded=True)
    assert decide(done, show("a", 6_100)) == Step(queue=cue("b", 0))
    already = feed("a", 0, 6_000, encoded=True, queued=cue("b", 0))
    assert decide(already, show("a", 6_100)) == Step()


def test_the_last_slot_of_a_counted_show_queues_nothing() -> None:
    once = Timeline(items=(A, B, C), loop_mode="count", loop_count=1)
    done = feed("c", 0, 5_000, encoded=True)
    assert decide(done, show("c", 5_100, timeline=once)) == Step()
    looping = decide(done, show("c", 5_100))
    assert looping.queue is not None and looping.queue.slot == ("body", "a", 0, 1)


def test_a_slow_start_is_given_time_before_it_is_judged() -> None:
    slow = feed("a", 2_000, 2_000, sending=False, started_ms=NOW - 3_000)
    assert decide(slow, show("a", 5_000)) == Step()
    stuck = feed("a", 2_000, 2_000, sending=False, started_ms=NOW - STARTUP_GRACE_MS)
    assert decide(stuck, show("a", 7_000)) == Step(restart=cue("a", 7_000))
    # A control action does not wait for a slow start.
    assert decide(slow, show("c", 0)) == Step(restart=cue("c", 0))


def test_a_failed_slot_is_retried_after_a_pause() -> None:
    failed = Failure(("body", "a", 0, 0), NOW - 500)
    assert decide(None, show("a", 3_000), failed) == Step()
    stale = Failure(("body", "a", 0, 0), NOW - RETRY_MS)
    assert decide(None, show("a", 3_000), stale) == Step(restart=cue("a", 3_000))
    other = Failure(("body", "b", 1, 0), NOW - 500)
    assert decide(None, show("a", 3_000), other) == Step(restart=cue("a", 3_000))
