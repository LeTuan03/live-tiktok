"""Scripted head and face motion for the offline motion clip.

Instead of copying motion from a driving video, the clip is scripted directly
in LivePortrait's motion space: head rotation in degrees, a small translation,
and deltas on the 21 implicit expression keypoints (blink, eyebrow, gaze,
smile). The mouth stays neutral because the realtime lip sync repaints it.

A clip has two **behavior tracks** over one shared timeline:

* ``IDLE``, the host between sentences: calm sway, glances down at the
  comments, small listening nods, a smile now and then;
* ``TALK``, the host speaking to camera: nods on a beat, head tilts, raised
  brows for emphasis, a look aside and back, a bigger smile, more blinks, and
  a fast low-amplitude "speech" micro-motion.

Both tracks are the same base motion plus a track-specific layer. The timeline
is cut into slots of 1.2 to 2.1 s, and every gesture is a smooth bump that
starts and ends inside its slot. At slot boundaries both layers are exactly
zero, so the two tracks show the same pose there: these are the *portals*
where the realtime generator can switch track without a visible jump.

Everything is periodic in the clip length, so each track loops with no seam.

Sign conventions (checked by rendering): positive pitch tilts the head down,
positive yaw turns the face toward the left of the image, positive pupil_x
moves the eyes toward the right of the image, negative pupil_y looks down and
lowers the lids. The expression keypoint indices follow the widely used
"expression editor" mapping (eyes 11/13/15/16, brows 1/2, mouth 14/17/20).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float32]

IDLE = 0
TALK = 1
TRACK_NAMES = ("idle", "talk")

# Blink start times as fractions of the clip, shared by both tracks.
_BLINKS = (0.07, 0.29, 0.47, 0.63, 0.81, 0.92)
_BLINK_SECONDS = 0.28
_BLINK_DEPTH = 16.0  # eyelid closure in expression units, about fully closed

_SLOT_MIN_S = 1.2
_SLOT_MAX_S = 2.1
_DEFAULT_SEED = 7

_CHANNELS = ("pitch", "yaw", "roll", "tx", "ty", "smile", "brow", "gaze_x", "gaze_y", "blink")


@dataclass(frozen=True)
class MotionFrame:
    """Motion offsets for one frame, relative to the source portrait."""

    pitch: float
    yaw: float
    roll: float
    tx: float
    ty: float
    exp: FloatArray
    """(21, 3) delta added to the source expression keypoints."""


def _wave(phase: float, terms: tuple[tuple[int, float, float], ...]) -> float:
    """Sum of sines; each term is (cycles per clip, amplitude, phase offset)."""
    return sum(amp * math.sin(2 * math.pi * (k * phase + off)) for k, amp, off in terms)


def bump(u: float, center: float, width: float) -> float:
    """Smooth bump on the slot's unit interval: 1 at ``center``, 0 beyond ``width / 2``.

    ``cos^2`` has zero slope at its ends, so a gesture eases in and out.
    """
    x = (u - center) / (width / 2)
    if abs(x) >= 1.0:
        return 0.0
    return math.cos(x * math.pi / 2) ** 2


def _blink_curve(seconds: float, starts: list[float]) -> float:
    value = 0.0
    for start in starts:
        local = (seconds - start) / _BLINK_SECONDS
        if 0.0 <= local <= 1.0:
            value = max(value, _BLINK_DEPTH * math.sin(math.pi * local) ** 2)
    return value


def expression(
    eyes: float, eyebrow: float, pupil_x: float, smile: float, pupil_y: float = 0.0
) -> FloatArray:
    exp = np.zeros((21, 3), dtype=np.float32)
    exp[20, 1] += smile * -0.01
    exp[14, 1] += smile * -0.02
    exp[17, 1] += smile * 0.0065
    exp[17, 2] += smile * 0.003
    exp[13, 1] += smile * -0.00275
    exp[16, 1] += smile * -0.00275
    exp[3, 1] += smile * -0.0035
    exp[7, 1] += smile * -0.0035

    if pupil_x > 0:
        exp[11, 0] += pupil_x * 0.0007
        exp[15, 0] += pupil_x * 0.001
    else:
        exp[11, 0] += pupil_x * 0.001
        exp[15, 0] += pupil_x * 0.0007

    # Looking down also lowers the lids a little, as real eyes do.
    exp[11, 1] += pupil_y * -0.001
    exp[15, 1] += pupil_y * -0.001

    exp[11, 1] += eyes * 0.0003
    exp[13, 1] += eyes * -0.0003
    exp[15, 1] += eyes * 0.0003
    exp[16, 1] += eyes * -0.0003
    exp[1, 1] += eyes * -0.00025
    exp[2, 1] += eyes * 0.00025

    if eyebrow > 0:
        exp[1, 1] += eyebrow * 0.001
        exp[2, 1] += eyebrow * -0.001
    else:
        exp[1, 0] += eyebrow * -0.001
        exp[2, 0] += eyebrow * 0.001
        exp[1, 1] += eyebrow * 0.0003
        exp[2, 1] += eyebrow * -0.0003
    return exp


def base_motion(phase: float) -> dict[str, float]:
    """Gentle sway shared by both tracks (the old idle loop, a little calmer)."""
    return {
        "yaw": _wave(phase, ((1, 3.2, 0.00), (2, 1.5, 0.31), (5, 0.6, 0.12))),
        "pitch": _wave(phase, ((1, 1.6, 0.27), (3, 1.0, 0.05), (7, 0.4, 0.40))),
        "roll": _wave(phase, ((2, 1.2, 0.60), (3, 0.6, 0.18))),
        "tx": _wave(phase, ((1, 0.005, 0.52), (3, 0.0025, 0.10))),
        "ty": _wave(phase, ((2, 0.003, 0.33), (4, 0.0015, 0.70))),
        "brow": max(0.0, _wave(phase, ((3, 2.0, 0.05), (2, 1.2, 0.45)))),
        "gaze_x": _wave(phase, ((2, 1.5, 0.15), (5, 1.0, 0.55))),
        "smile": 0.24 + _wave(phase, ((1, 0.06, 0.10), (3, 0.03, 0.66))),
    }


# ----------------------------------------------------------------- gestures
#
# A gesture maps (u in [0, 1] across its slot, side = +1 or -1, strength in
# [0.75, 1.15]) to channel offsets. Every term is a `bump` placed so that it
# is exactly zero at u = 0 and u = 1.

Gesture = Callable[[float, float, float], dict[str, float]]


def _talk_nod(u: float, side: float, k: float) -> dict[str, float]:
    # A beat: a firm dip, a smaller second one, brows up just before.
    return {
        "pitch": k * (5.5 * bump(u, 0.33, 0.34) + 3.0 * bump(u, 0.66, 0.28)),
        "brow": k * 7.0 * bump(u, 0.28, 0.4),
        "roll": side * k * 1.5 * bump(u, 0.5, 0.8),
    }


def _talk_tilt(u: float, side: float, k: float) -> dict[str, float]:
    # Head cocked to one side with a warmer smile: friendly, conversational.
    return {
        "roll": side * k * 5.0 * bump(u, 0.5, 0.82),
        "yaw": side * k * 2.5 * bump(u, 0.5, 0.8),
        "pitch": -k * 1.5 * bump(u, 0.45, 0.7),
        "smile": k * 0.18 * bump(u, 0.5, 0.8),
        "brow": k * 4.0 * bump(u, 0.4, 0.5),
    }


def _talk_emphasis(u: float, side: float, k: float) -> dict[str, float]:
    # "Really!": chin up with raised brows and wide eyes, then a dip on the beat.
    return {
        "pitch": k * (-3.5 * bump(u, 0.3, 0.38) + 4.5 * bump(u, 0.66, 0.3)),
        "brow": k * 15.0 * bump(u, 0.32, 0.46),
        "gaze_y": k * 3.0 * bump(u, 0.32, 0.4),
        "yaw": side * k * 1.5 * bump(u, 0.5, 0.8),
    }


def _talk_aside(u: float, side: float, k: float) -> dict[str, float]:
    # Turns the head away as if pointing something out, eyes lead, then back.
    return {
        "yaw": side * k * 7.0 * bump(u, 0.5, 0.8),
        "gaze_x": -side * k * 7.0 * bump(u, 0.45, 0.6),
        "pitch": k * 2.0 * bump(u, 0.5, 0.7),
        "roll": side * k * 1.5 * bump(u, 0.5, 0.7),
    }


def _talk_smile(u: float, side: float, k: float) -> dict[str, float]:
    # A bright moment: big smile, chin lifts a little, head sways.
    return {
        "smile": k * 0.3 * bump(u, 0.5, 0.84),
        "pitch": -k * 2.5 * bump(u, 0.45, 0.6),
        "roll": side * k * 2.5 * bump(u, 0.5, 0.8),
        "brow": k * 5.0 * bump(u, 0.45, 0.5),
    }


def _talk_engage(u: float, side: float, k: float) -> dict[str, float]:
    # Straight to camera: small double nod, brows active, eyes on the lens.
    return {
        "pitch": k * (2.5 * bump(u, 0.3, 0.26) + 2.5 * bump(u, 0.62, 0.26)),
        "yaw": side * k * 2.0 * bump(u, 0.5, 0.8),
        "gaze_x": side * k * 1.5 * bump(u, 0.5, 0.8),
        "brow": k * 6.0 * bump(u, 0.55, 0.45),
    }


def _idle_rest(u: float, side: float, k: float) -> dict[str, float]:
    return {}


def _idle_read(u: float, side: float, k: float) -> dict[str, float]:
    # Glances down at the comments, then back up to camera.
    return {
        "pitch": k * 6.0 * bump(u, 0.5, 0.84),
        "gaze_y": -k * 4.5 * bump(u, 0.5, 0.7),
        "gaze_x": side * k * 2.0 * bump(u, 0.5, 0.7),
        "yaw": side * k * 1.5 * bump(u, 0.5, 0.8),
    }


def _idle_listen(u: float, side: float, k: float) -> dict[str, float]:
    # Small "mm-hm" nods.
    return {
        "pitch": k * (2.5 * bump(u, 0.35, 0.3) + 2.0 * bump(u, 0.65, 0.3)),
        "smile": k * 0.08 * bump(u, 0.5, 0.8),
    }


def _idle_smile(u: float, side: float, k: float) -> dict[str, float]:
    return {
        "smile": k * 0.2 * bump(u, 0.5, 0.84),
        "roll": side * k * 2.0 * bump(u, 0.5, 0.8),
        "brow": k * 3.0 * bump(u, 0.5, 0.6),
    }


def _idle_glance(u: float, side: float, k: float) -> dict[str, float]:
    # Looks off to the side (the studio, a product on the table), then back.
    return {
        "yaw": side * k * 4.0 * bump(u, 0.5, 0.8),
        "gaze_x": -side * k * 6.0 * bump(u, 0.48, 0.6),
    }


_TALK_GESTURES: tuple[Gesture, ...] = (
    _talk_nod,
    _talk_tilt,
    _talk_emphasis,
    _talk_aside,
    _talk_smile,
    _talk_engage,
)
_IDLE_GESTURES: tuple[Gesture, ...] = (
    _idle_rest,
    _idle_read,
    _idle_listen,
    _idle_smile,
    _idle_glance,
)

TALK_GESTURES: dict[str, Gesture] = {
    "nod": _talk_nod,
    "tilt": _talk_tilt,
    "emphasis": _talk_emphasis,
    "aside": _talk_aside,
    "smile": _talk_smile,
    "engage": _talk_engage,
}
"""Talk gestures by name, for callers that script them (the ad video director)."""
IDLE_GESTURES: dict[str, Gesture] = {
    "rest": _idle_rest,
    "read": _idle_read,
    "listen": _idle_listen,
    "smile": _idle_smile,
    "glance": _idle_glance,
}


@dataclass(frozen=True)
class _Slot:
    start: float
    duration: float
    gestures: tuple[Gesture, Gesture]
    """(idle, talk) gesture for this slot."""
    side: float
    strength: float
    blinks: tuple[float, ...]
    """Extra blink start times (seconds) on the talk track inside this slot."""


def _sequence(rng: np.random.Generator, options: tuple[Gesture, ...], count: int) -> list[Gesture]:
    """``count`` gestures drawn as shuffled rounds of ``options``: every gesture
    shows up before any repeats, and none follows itself (also across the loop)."""
    out: list[Gesture] = []
    while len(out) < count:
        round_ = [options[int(i)] for i in rng.permutation(len(options))]
        if out and round_[0] is out[-1]:
            round_.append(round_.pop(0))
        out.extend(round_)
    out = out[:count]
    if count > 2 and out[-1] is out[0]:
        out[-1], out[-2] = out[-2], out[-1]
    return out


@dataclass(frozen=True)
class MotionScript:
    """Both behavior tracks of one looping clip."""

    total: int
    fps: float
    seed: int = _DEFAULT_SEED
    _slots: list[_Slot] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        rng = np.random.default_rng(self.seed)
        clip_seconds = self.total / self.fps
        count = max(1, round(clip_seconds / ((_SLOT_MIN_S + _SLOT_MAX_S) / 2)))
        raw = rng.uniform(_SLOT_MIN_S, _SLOT_MAX_S, count)
        durations = raw * (clip_seconds / raw.sum())
        slots: list[_Slot] = []
        start = 0.0
        idle_seq = _sequence(rng, _IDLE_GESTURES, count)
        talk_seq = _sequence(rng, _TALK_GESTURES, count)
        for duration, idle_g, talk_g in zip(durations, idle_seq, talk_seq, strict=True):
            # People blink more while talking, mostly at the start of a phrase.
            blinks: tuple[float, ...] = ()
            if rng.random() < 0.55:
                blinks = (start + duration * float(rng.uniform(0.15, 0.7)),)
            slots.append(
                _Slot(
                    start=float(start),
                    duration=float(duration),
                    gestures=(idle_g, talk_g),
                    side=1.0 if rng.random() < 0.5 else -1.0,
                    strength=float(rng.uniform(0.75, 1.15)),
                    blinks=blinks,
                )
            )
            start += duration
        object.__setattr__(self, "_slots", slots)

    @property
    def portal_seconds(self) -> list[float]:
        """Slot boundaries, where both tracks show exactly the same pose."""
        return [s.start for s in self._slots]

    def _slot_at(self, seconds: float) -> _Slot:
        for slot in reversed(self._slots):
            if seconds >= slot.start:
                return slot
        return self._slots[0]

    def channels(self, track: int, index: int) -> dict[str, float]:
        """Motion channels for frame ``index`` of ``track``, before conversion."""
        index %= self.total
        phase = index / self.total
        seconds = index / self.fps
        values = dict.fromkeys(_CHANNELS, 0.0)
        values.update(base_motion(phase))
        blinks = [start * self.total / self.fps for start in _BLINKS]

        slot = self._slot_at(seconds)
        u = (seconds - slot.start) / slot.duration
        gesture = slot.gestures[track]
        for name, value in gesture(u, slot.side, slot.strength).items():
            values[name] += value

        if track == TALK:
            blinks += list(slot.blinks)
            # Fast, small head motion that goes with articulation. Enveloped by
            # the slot so it is zero at the portals.
            envelope = bump(u, 0.5, 1.0)
            values["pitch"] += envelope * 0.7 * math.sin(2 * math.pi * 3.1 * seconds)
            values["yaw"] += envelope * 0.5 * math.sin(2 * math.pi * 2.3 * seconds + 1.1)
            values["roll"] += envelope * 0.3 * math.sin(2 * math.pi * 1.7 * seconds + 0.4)
        values["blink"] = _blink_curve(seconds, blinks)
        return values

    def frame(self, track: int, index: int) -> MotionFrame:
        v = self.channels(track, index)
        return MotionFrame(
            pitch=v["pitch"],
            yaw=v["yaw"],
            roll=v["roll"],
            tx=v["tx"],
            ty=v["ty"],
            exp=expression(v["blink"], v["brow"], v["gaze_x"], v["smile"], v["gaze_y"]),
        )

    def portals(self, tolerance: float = 0.35) -> npt.NDArray[np.bool_]:
        """Frames where every track shows nearly the same pose, shape ``(total,)``.

        ``tolerance`` is in degrees of head rotation; expression differences
        are weighted so a visible change in brows, gaze or smile also counts.
        """
        weights = {"smile": 20.0, "brow": 0.15, "gaze_x": 0.15, "gaze_y": 0.15, "blink": 0.1}
        out = np.zeros(self.total, dtype=bool)
        for index in range(self.total):
            idle = self.channels(IDLE, index)
            talk = self.channels(TALK, index)
            gap = sum(abs(idle[c] - talk[c]) for c in ("pitch", "yaw", "roll"))
            gap += sum(w * abs(idle[c] - talk[c]) for c, w in weights.items())
            out[index] = gap <= tolerance
        return out

    def shared(self, index: int) -> bool:
        """True when both tracks render the identical frame (render it once)."""
        idle = self.channels(IDLE, index)
        talk = self.channels(TALK, index)
        return all(abs(idle[c] - talk[c]) < 1e-9 for c in _CHANNELS)


def idle_motion(index: int, total: int, fps: float) -> MotionFrame:
    """Motion for frame ``index`` of the idle track of a ``total``-frame loop."""
    return MotionScript(total, fps).frame(IDLE, index)
