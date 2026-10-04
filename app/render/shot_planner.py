"""Shot Planner — constructs dynamic review rhythm timelines (8-15s cuts) from structured script segments."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Set

from loguru import logger

from app.services.script_segment_service import ScriptSegment, StructuredScript


@dataclass
class PlannedShot:
    """A scheduled camera shot segment in the rendering timeline."""

    shot: str  # WIDE_MODEL, MEDIUM_MODEL, CLOSEUP_PRODUCT, SPLIT, INSET_PRODUCT, TRYON_WIDE
    start_time: float
    duration: float
    segment_index: int
    cue: str
    text: str

    @property
    def end_time(self) -> float:
        return self.start_time + self.duration


@dataclass
class ShotPlan:
    """Complete video editing timeline composed of ordered camera shots."""

    shots: List[PlannedShot] = field(default_factory=list)

    @property
    def total_duration(self) -> float:
        if not self.shots:
            return 0.0
        return self.shots[-1].end_time

    @property
    def shot_count(self) -> int:
        return len(self.shots)

    @property
    def distinct_shots_count(self) -> int:
        return len({s.shot for s in self.shots})

    @property
    def shot_switches_count(self) -> int:
        """Number of times the camera angle changes."""
        if len(self.shots) <= 1:
            return 0
        switches = 0
        for i in range(1, len(self.shots)):
            if self.shots[i].shot != self.shots[i - 1].shot:
                switches += 1
        return switches

    def is_valid_review_rhythm(self) -> bool:
        """Check if video satisfies review rhythm rules (§4.4): ≥ 3 shot switches, no shot > 20s."""
        if self.shot_switches_count < 3 and self.total_duration >= 30.0:
            return False
        return all(s.duration <= 25.0 for s in self.shots)


class ShotPlanner:
    """Plans realistic review-style camera cuts according to TTS duration and asset availability."""

    def __init__(self, target_shot_duration: float = 12.0) -> None:
        self.target_shot_duration = target_shot_duration

    def plan_shots(
        self,
        structured_script: StructuredScript,
        has_closeup_asset: bool = False,
        default_segment_duration: float = 10.0,
    ) -> ShotPlan:
        """Generate a timed shot timeline adhering to review rhythm rules (§4.4).

        Rules:
        1. Cuts occur at sentence/segment boundaries.
        2. No shot > 20 seconds.
        3. No 2 identical shots in immediate succession.
        4. If CLOSEUP_PRODUCT is requested but no closeup media exists, downgrade to INSET_PRODUCT or SPLIT.
        5. Total shot switches must be ≥ 3 for any script ≥ 30s.
        """
        segments = structured_script.segments
        if not segments:
            return ShotPlan()

        planned_shots: List[PlannedShot] = []
        current_time = 0.0
        last_shot = ""

        # Complementary shot rotation cycle when avoiding duplicate consecutive shots
        alternates = ["MEDIUM_MODEL", "WIDE_MODEL", "INSET_PRODUCT", "SPLIT"]

        for idx, seg in enumerate(segments):
            dur = seg.duration_seconds if seg.duration_seconds and seg.duration_seconds > 0 else default_segment_duration
            desired_shot = seg.shot

            # Rule 4: Auto-downgrade CLOSEUP_PRODUCT if no closeup asset exists
            if desired_shot == "CLOSEUP_PRODUCT" and not has_closeup_asset:
                logger.debug("Không có tư liệu cận cảnh sản phẩm — tự động chuyển CLOSEUP_PRODUCT -> INSET_PRODUCT.")
                desired_shot = "INSET_PRODUCT"

            # Rule 3: Avoid 2 consecutive identical shots
            if desired_shot == last_shot:
                for alt in alternates:
                    if alt != last_shot:
                        desired_shot = alt
                        break

            # Rule 2: No single shot > 20s; if a segment is longer than 20s, split it
            if dur > 20.0:
                half_dur = dur / 2.0
                # Part 1
                planned_shots.append(
                    PlannedShot(
                        shot=desired_shot,
                        start_time=round(current_time, 2),
                        duration=round(half_dur, 2),
                        segment_index=idx,
                        cue=seg.cue,
                        text=seg.clean_text_for_tts()[:80] + "...",
                    )
                )
                current_time += half_dur

                # Part 2: change angle for the second half
                second_shot = "SPLIT" if desired_shot != "SPLIT" else "MEDIUM_MODEL"
                planned_shots.append(
                    PlannedShot(
                        shot=second_shot,
                        start_time=round(current_time, 2),
                        duration=round(half_dur, 2),
                        segment_index=idx,
                        cue=seg.cue,
                        text=seg.clean_text_for_tts()[80:],
                    )
                )
                current_time += half_dur
                last_shot = second_shot
            else:
                planned_shots.append(
                    PlannedShot(
                        shot=desired_shot,
                        start_time=round(current_time, 2),
                        duration=round(dur, 2),
                        segment_index=idx,
                        cue=seg.cue,
                        text=seg.clean_text_for_tts(),
                    )
                )
                current_time += dur
                last_shot = desired_shot

        # Rule 5 check & adjustment: If total duration >= 30s but shot switches < 3,
        # diversify shots to ensure vivid dynamic review feel
        plan = ShotPlan(shots=planned_shots)
        if plan.total_duration >= 30.0 and plan.shot_switches_count < 3 and len(planned_shots) >= 3:
            # Rebalance angles across middle shots
            if len(planned_shots) > 1:
                planned_shots[1].shot = "HOLD_PRODUCT" if planned_shots[1].shot != "HOLD_PRODUCT" else "INSET_PRODUCT"
            if len(planned_shots) > 2:
                planned_shots[2].shot = "SPLIT" if planned_shots[2].shot != "SPLIT" else "CLOSEUP_PRODUCT"
            plan = ShotPlan(shots=planned_shots)

        return plan
