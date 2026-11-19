"""Control-room actions mapped onto the timeline (pure, no database)."""

from __future__ import annotations

import pytest

from soshop.broadcast.control import ControlError, plan
from soshop.broadcast.timeline import Playhead, Slot, Timeline

A, B = Slot("a", 10_000), Slot("b", 20_000)
OUTRO = Slot("outro", 4_000)


def test_start_needs_a_playable_product_video() -> None:
    with pytest.raises(ControlError):
        plan("start", Timeline(), "idle", None, 0)


def test_start_from_the_top_or_from_a_chosen_video() -> None:
    tl = Timeline(items=(A, B), intro=Slot("intro", 3_000))
    top = plan("start", tl, "idle", None, 1_000)
    assert top.status == "live" and top.started
    assert top.playhead is not None and top.playhead.item_id == "intro"
    assert top.playhead.at_ms == 1_000 and top.playhead.playing
    chosen = plan("start", tl, "ended", None, 0, item_id="b")
    assert chosen.playhead is not None and chosen.playhead.item_id == "b"
    with pytest.raises(ControlError):
        plan("start", tl, "idle", None, 0, item_id="nope")


def test_pause_and_resume_freeze_and_release_the_position() -> None:
    tl = Timeline(items=(A, B))
    live = plan("start", tl, "idle", None, 0).playhead
    paused = plan("pause", tl, "live", live, 12_000)
    assert paused.status == "paused" and paused.playhead is not None
    assert (paused.playhead.item_id, paused.playhead.offset_ms) == ("b", 2_000)
    assert not paused.playhead.playing
    resumed = plan("resume", tl, "paused", paused.playhead, 99_000)
    assert resumed.status == "live" and resumed.playhead is not None
    assert (resumed.playhead.offset_ms, resumed.playhead.at_ms) == (2_000, 99_000)


def test_next_keeps_a_paused_show_paused() -> None:
    tl = Timeline(items=(A, B))
    paused = Playhead(phase="body", item_id="a", offset_ms=3_000, at_ms=0, playing=False)
    moved = plan("next", tl, "paused", paused, 50_000)
    assert moved.status == "paused" and moved.playhead is not None
    assert (moved.playhead.item_id, moved.playhead.offset_ms) == ("b", 0)


def test_next_past_the_last_pass_ends_the_show() -> None:
    tl = Timeline(items=(A, B), loop_mode="count", loop_count=1)
    on_b = Playhead(phase="body", item_id="b", index=1, at_ms=0)
    assert plan("next", tl, "live", on_b, 1_000).status == "ended"


def test_finish_plays_the_outro_or_ends() -> None:
    live = Playhead(phase="body", item_id="a", at_ms=0)
    with_outro = plan("finish", Timeline(items=(A, B), outro=OUTRO), "live", live, 1_000)
    assert with_outro.status == "live" and with_outro.playhead is not None
    assert with_outro.playhead.phase == "outro"
    assert plan("finish", Timeline(items=(A, B)), "live", live, 1_000).status == "ended"


def test_stop_ends_at_once_and_idle_shows_reject_moves() -> None:
    tl = Timeline(items=(A,))
    assert plan("stop", tl, "live", Playhead(phase="body", item_id="a", at_ms=0), 0).status == (
        "ended"
    )
    for action in ("pause", "resume", "next", "prev", "finish"):
        with pytest.raises(ControlError):
            plan(action, tl, "idle", None, 0)  # type: ignore[arg-type]


def test_jump_to_unknown_video_is_rejected() -> None:
    tl = Timeline(items=(A, B))
    live = Playhead(phase="body", item_id="a", at_ms=0)
    with pytest.raises(ControlError):
        plan("jump", tl, "live", live, 0, item_id="zzz")
    jumped = plan("jump", tl, "live", live, 0, item_id="b")
    assert jumped.playhead is not None and jumped.playhead.item_id == "b"


def test_a_show_that_already_ran_out_reports_ended() -> None:
    tl = Timeline(items=(A,), loop_mode="count", loop_count=1)
    old = Playhead(phase="body", item_id="a", at_ms=0)
    assert plan("pause", tl, "live", old, 60_000).status == "ended"
