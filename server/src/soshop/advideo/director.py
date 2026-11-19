"""Per-frame performance of the host, planned from the script and the voice.

Where the live avatar loops a prebuilt clip, an offline render can follow the
words. Each frame gets:

* a slow base sway shared with the live clip (``motion.base_motion``);
* gestures in slots of about 1.6 s laid over each spoken beat. The first slot
  of a beat plays the gesture the script asked for, later slots rotate
  through the rest of the repertoire; pauses get calm idle gestures. Every
  gesture is a bump that is zero at its slot edges, so slots chain smoothly;
* a quick nod in 3D on each stressed syllable (the live layer's accent
  detector, now driving real head pitch instead of a 2D shift);
* small fast head motion while speaking, which goes with articulation;
* blinks just before each phrase starts and every few seconds otherwise;
* the mouth opening from ``mouth.openness``, as a LivePortrait lip ratio.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from soshop.advideo.mouth import openness
from soshop.advideo.script import AdScript
from soshop.advideo.voice import VoiceTrack, frame_count
from soshop.agent.lipsync.motion import (
    IDLE_GESTURES,
    TALK_GESTURES,
    Gesture,
    MotionFrame,
    base_motion,
    bump,
    expression,
)
from soshop.agent.lipsync.performance import NOD_SECONDS, AccentDetector, loudness, nod_shape

FPS = 25
_SUB_SLOT_S = 1.6
_MIN_IDLE_GESTURE_S = 0.8
_BASE_PERIOD_S = 17.0
_MAX_LIP = 0.30
_LIP_GAMMA = 0.9
_NOD_DEG = 3.5
_TALK_MARGIN_S = 0.08
_BLINK_S = 0.28
_BLINK_DEPTH = 16.0
_BLINK_LEAD_S = 0.08
_BLINK_MIN_GAP_S = 0.9
_BLINK_EVERY_S = (2.2, 4.2)
_LOOKAHEAD = (1, 3)
"""Frames ahead whose loudness drives the nods, as in the live generator."""

_CHANNELS = ("pitch", "yaw", "roll", "tx", "ty", "smile", "brow", "gaze_x", "gaze_y")


@dataclass(frozen=True)
class DirectedFrame:
    motion: MotionFrame
    lip: float
    """LivePortrait lip ratio: 0 closed, about 0.3 a wide open vowel."""
    talking: bool


@dataclass(frozen=True)
class Slot:
    start: float
    end: float
    gesture: Gesture
    side: float
    strength: float
    talking: bool

    def offsets(self, seconds: float) -> dict[str, float]:
        u = (seconds - self.start) / max(self.end - self.start, 1e-6)
        return self.gesture(u, self.side, self.strength)


def _idle_slot(start: float, end: float, name: str, rng: np.random.Generator) -> Slot:
    if end - start < _MIN_IDLE_GESTURE_S:
        name = "rest"
    side = 1.0 if rng.random() < 0.5 else -1.0
    return Slot(start, end, IDLE_GESTURES[name], side, float(rng.uniform(0.8, 1.0)), False)


def _rotation(rng: np.random.Generator) -> list[str]:
    names = [n for n in TALK_GESTURES if n != "aside"]
    return [names[int(i)] for i in rng.permutation(len(names))]


def plan_slots(track: VoiceTrack, script: AdScript, rng: np.random.Generator) -> list[Slot]:
    """Gesture slots covering the whole video, in time order."""
    slots: list[Slot] = []
    cursor = 0.0
    pool: list[str] = []
    for spoken in track.beats:
        if spoken.start - cursor > 0.05:
            name = "smile" if cursor == 0.0 else "rest"
            slots.append(_idle_slot(cursor, spoken.start, name, rng))
        wanted = script.beats[spoken.index].gesture
        count = max(1, round((spoken.end - spoken.start) / _SUB_SLOT_S))
        step = (spoken.end - spoken.start) / count
        previous = None
        for k in range(count):
            if k == 0:
                name = wanted
            else:
                pool = pool or _rotation(rng)
                name = pool.pop()
                if name == previous:
                    pool = pool or _rotation(rng)
                    name = pool.pop()
            previous = name
            slots.append(
                Slot(
                    spoken.start + k * step,
                    spoken.start + (k + 1) * step,
                    TALK_GESTURES[name],
                    1.0 if rng.random() < 0.5 else -1.0,
                    float(rng.uniform(0.85, 1.15)),
                    True,
                )
            )
        cursor = spoken.end
    if track.duration - cursor > 0.05:
        slots.append(_idle_slot(cursor, track.duration, "smile", rng))
    return slots


def blink_times(track: VoiceTrack, rng: np.random.Generator) -> list[float]:
    """Blink start times: as each phrase begins, and every few seconds between."""
    wanted = [max(0.0, beat.start - _BLINK_LEAD_S) for beat in track.beats]
    t = float(rng.uniform(0.3, 1.2))
    while t < track.duration:
        wanted.append(t)
        t += float(rng.uniform(*_BLINK_EVERY_S))
    times: list[float] = []
    for start in sorted(wanted):
        if not times or start - times[-1] >= _BLINK_MIN_GAP_S:
            times.append(start)
    return times


def nod_events(track: VoiceTrack, fps: int) -> list[tuple[float, float]]:
    """(time, strength) of a nod on each stressed syllable."""
    detector = AccentDetector(1.0 / fps)
    per_frame = track.rate // fps
    events: list[tuple[float, float]] = []
    for i in range(frame_count(track, fps)):
        seconds = i / fps
        level = None
        if track.beat_at(seconds) is not None:
            a = (i + _LOOKAHEAD[0]) * per_frame
            b = (i + _LOOKAHEAD[1]) * per_frame
            level = loudness(track.samples[a:b].tobytes())
        strength = detector.update(level)
        if strength is not None:
            events.append((seconds, strength))
    return events


def _blink(seconds: float, times: list[float]) -> float:
    value = 0.0
    for start in times:
        local = (seconds - start) / _BLINK_S
        if 0.0 <= local <= 1.0:
            value = max(value, _BLINK_DEPTH * math.sin(math.pi * local) ** 2)
    return value


def _speech_micro(slot: Slot, seconds: float) -> dict[str, float]:
    u = (seconds - slot.start) / max(slot.end - slot.start, 1e-6)
    envelope = bump(u, 0.5, 1.0)
    return {
        "pitch": envelope * 0.7 * math.sin(2 * math.pi * 3.1 * seconds),
        "yaw": envelope * 0.5 * math.sin(2 * math.pi * 2.3 * seconds + 1.1),
        "roll": envelope * 0.3 * math.sin(2 * math.pi * 1.7 * seconds + 0.4),
    }


class _Plan:
    def __init__(self, track: VoiceTrack, script: AdScript, fps: int, seed: int) -> None:
        rng = np.random.default_rng(seed)
        self.track = track
        self.slots = plan_slots(track, script, rng)
        self.blinks = blink_times(track, rng)
        self.nods = nod_events(track, fps)
        total = frame_count(track, fps)
        opens = openness(track.samples, track.rate, fps)
        self.opens = np.pad(opens, (0, max(0, total - len(opens))))[:total]

    def slot_at(self, seconds: float) -> Slot:
        for slot in reversed(self.slots):
            if seconds >= slot.start:
                return slot
        return self.slots[0]

    def talking(self, seconds: float) -> bool:
        return any(
            b.start - _TALK_MARGIN_S <= seconds < b.end + _TALK_MARGIN_S for b in self.track.beats
        )

    def channels(self, seconds: float) -> dict[str, float]:
        values = dict.fromkeys(_CHANNELS, 0.0)
        values.update(base_motion(seconds / _BASE_PERIOD_S))
        slot = self.slot_at(seconds)
        extras = [slot.offsets(seconds)]
        if slot.talking:
            extras.append(_speech_micro(slot, seconds))
        for extra in extras:
            for name, value in extra.items():
                values[name] += value
        values["pitch"] += sum(
            _NOD_DEG * s * nod_shape((seconds - t0) / NOD_SECONDS) for t0, s in self.nods
        )
        return values


def direct(
    track: VoiceTrack, script: AdScript, fps: int = FPS, seed: int = 7
) -> list[DirectedFrame]:
    """The host's performance for every frame of ``track``."""
    plan = _Plan(track, script, fps, seed)
    frames: list[DirectedFrame] = []
    for i in range(len(plan.opens)):
        seconds = i / fps
        v = plan.channels(seconds)
        talking = plan.talking(seconds)
        lip = _MAX_LIP * float(plan.opens[i]) ** _LIP_GAMMA if talking else 0.0
        exp = expression(
            _blink(seconds, plan.blinks), v["brow"], v["gaze_x"], v["smile"], v["gaze_y"]
        )
        motion = MotionFrame(v["pitch"], v["yaw"], v["roll"], v["tx"], v["ty"], exp)
        frames.append(DirectedFrame(motion, lip, talking))
    return frames
