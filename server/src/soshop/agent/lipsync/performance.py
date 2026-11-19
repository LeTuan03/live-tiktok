"""Realtime body, head and camera motion layered over the motion clip.

The prebuilt clip only animates the face crop: shoulders and torso are the
original photo, and the clip cannot react to what is being said. This layer
fixes both with one smooth warp of the finished frame (after the lip sync):

* **breathing**: shoulders and chest rise and fall, faster while talking;
* **body sway**: the upper body drifts from side to side around a pivot below
  the frame, as a seated person does;
* **lean in**: while the host talks the upper body comes a little toward the
  camera, and settles back when she stops;
* **beat nods**: stressed syllables in the speech (a jump in loudness over the
  running level) kick a short head dip, timed from audio that has not been
  played yet so the nod lands on the syllable, not after it;
* **camera**: a slow handheld drift (translation, roll, zoom) over the whole
  frame, so the background is not pixel-frozen like a still photo.

Body terms are weighted by a person matte (``matte.py``) when one is
available, so the person moves and the background stays put; without a matte
they fall back to a rough geometric body region.

The displacement field is computed on a coarse grid and upsampled, then
applied with one ``cv2.remap``: about 4 ms per 540x960 frame.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float32]
ImageArray = npt.NDArray[np.uint8]

_GRID_STEP = 8
"""Coarse grid spacing in pixels. The field is smooth, so this is plenty."""

# Amplitudes are fractions of the frame height (960 px for the default clip).
_BREATH_RISE = 0.0026
_SWAY = (0.0028, 0.0045)
"""Side-to-side sway at head height: (idle, talking)."""
_LEAN_SCALE = 0.016
_NOD_PEAK = 0.0042
_CAMERA_SHIFT = 0.0022
_CAMERA_ROLL_DEG = 0.22
_CAMERA_ZOOM = 1.028
"""Base zoom: enough overscan that drift and sway never show the frame edge."""
_CAMERA_ZOOM_DRIFT = 0.006

_BREATH_HZ = (0.22, 0.30)
_ACTIVITY_TAU_S = 0.7

# Beat detection on the upcoming speech.
_LOUD_FLOOR_DB = -42.0
_LOUD_RANGE_DB = 30.0
_LEVEL_ATTACK_S = 0.12
_LEVEL_RELEASE_S = 0.6
"""The running level follows a rise quickly (so one loud word triggers one
nod, not several while the average catches up) and decays slowly."""
_ACCENT_MIN = 0.15
_ACCENT_LOUDNESS_MIN = 0.45
_NOD_REFRACTORY_S = 0.6
_NOD_SECONDS = 0.45
_NOD_DOWN = 0.35
"""A nod takes 0.45 s: down over the first 35 %, a slower return."""


@dataclass(frozen=True)
class FaceAnchor:
    """Where the face sits in the frame, in pixels."""

    cx: float
    cy: float
    width: float
    height: float

    @classmethod
    def from_boxes(cls, boxes: npt.NDArray[np.int32]) -> FaceAnchor:
        median = np.median(np.asarray(boxes, dtype=np.float64), axis=0)
        x0, y0, x1, y1 = (float(v) for v in median)
        return cls(cx=(x0 + x1) / 2, cy=(y0 + y1) / 2, width=x1 - x0, height=y1 - y0)


def loudness(pcm: bytes) -> float | None:
    """RMS level of 16-bit mono PCM in dBFS, ``None`` for an empty chunk."""
    samples = np.frombuffer(pcm, dtype=np.int16)
    if samples.size == 0:
        return None
    rms = float(np.sqrt(np.mean(np.square(samples.astype(np.float32) / 32768.0))))
    return 20.0 * math.log10(max(rms, 1e-6))


def _smoothstep(x: FloatArray) -> FloatArray:
    t = np.clip(x, 0.0, 1.0)
    return (t * t * (3.0 - 2.0 * t)).astype(np.float32)


def _drift(seconds: float, terms: tuple[tuple[float, float], ...]) -> float:
    """Smooth pseudo-random signal in [-1, 1]: incommensurate sines, never in
    step with the 12 s clip loop. Each term is (period in seconds, phase)."""
    total = sum(math.sin(2 * math.pi * seconds / period + phase) for period, phase in terms)
    return total / len(terms)


class AccentDetector:
    """Finds stressed syllables in a stream of per-frame speech levels.

    A stressed syllable is a jump in loudness over the running level of the
    speech. The level follows a rise quickly, so one loud word fires once
    rather than several times while an average catches up, and decays
    slowly through short pauses. A refractory period keeps the rate natural
    (about one accent every two seconds on typical TTS).
    """

    def __init__(self, dt: float) -> None:
        self._dt = dt
        self._level = 0.0
        self._since = 10.0

    def update(self, loud_db: float | None) -> float | None:
        """Feed one frame's level (dBFS, ``None`` for no speech).

        Returns the accent strength in ``[0.4, 1]`` when this frame starts one.
        """
        dt = self._dt
        self._since += dt
        if loud_db is None:
            self._level *= math.exp(-dt / _LEVEL_RELEASE_S)
            return None
        level = float(np.clip((loud_db - _LOUD_FLOOR_DB) / _LOUD_RANGE_DB, 0.0, 1.0))
        accent = level - self._level
        tau = _LEVEL_ATTACK_S if level > self._level else _LEVEL_RELEASE_S
        self._level += (level - self._level) * (1.0 - math.exp(-dt / tau))
        if (
            accent > _ACCENT_MIN
            and level > _ACCENT_LOUDNESS_MIN
            and self._since > _NOD_REFRACTORY_S
        ):
            self._since = 0.0
            return float(np.clip(accent / 0.3, 0.4, 1.0))
        return None


def nod_shape(u: float) -> float:
    """Nod profile over its duration, 0 at both ends and 1 at the bottom."""
    if u <= 0.0 or u >= 1.0:
        return 0.0
    if u < _NOD_DOWN:
        return math.sin(math.pi / 2 * u / _NOD_DOWN) ** 2
    return math.cos(math.pi / 2 * (u - _NOD_DOWN) / (1 - _NOD_DOWN)) ** 2


NOD_SECONDS = _NOD_SECONDS


class Performer:
    """Stateful per-frame warp. Call :meth:`step` once per frame, then :meth:`warp`.

    ``camera=False`` drops the handheld drift, for footage an editor frames
    afterwards (the offline ad video cuts and zooms on its own).
    """

    def __init__(
        self,
        width: int,
        height: int,
        fps: float,
        face: FaceAnchor,
        matte: FloatArray | None = None,
        *,
        camera: bool = True,
    ) -> None:
        self._width = width
        self._height = height
        self._dt = 1.0 / fps
        self._t = 0.0
        self._breath_phase = 0.0
        self._activity = 0.0
        self._camera = camera
        self._accents = AccentDetector(self._dt)
        self._nods: list[tuple[float, float]] = []
        """(start time, peak in pixels) of the nods in flight."""
        self._nod_peak = _NOD_PEAK * height
        self._nod_pos = 0.0
        self.enabled = True

        gw = math.ceil(width / _GRID_STEP) + 1
        gh = math.ceil(height / _GRID_STEP) + 1
        xs = np.linspace(0, width - 1, gw, dtype=np.float32)
        ys = np.linspace(0, height - 1, gh, dtype=np.float32)
        self._gx, self._gy = np.meshgrid(xs, ys)
        self._grid_size = (gw, gh)
        self._id_x, self._id_y = np.meshgrid(
            np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32)
        )

        person = self._person_weight(face, matte)
        # The face box runs from the hairline to just below the chin.
        chin = face.cy + face.height * 0.45
        shoulders = face.cy + face.height * 0.75
        # Breathing: full rise above the shoulders, none at the bottom edge.
        rise = _smoothstep((height - self._gy) / max(height - shoulders, 1.0))
        self._w_breath = person * rise
        # Sway pivots below the frame: 1 at the face, about 0.35 at the bottom.
        pivot = height * 1.55
        self._w_sway = person * np.clip((pivot - self._gy) / (pivot - face.cy), 0.0, 1.15)
        # Lean in: scale about the face center, fading out toward the bottom.
        self._lean_dx = (self._gx - face.cx) * self._w_breath
        self._lean_dy = (self._gy - face.cy) * self._w_breath
        # Nods move the whole head down to the chin; the neck takes up the
        # difference and the shoulders stay where they are.
        across = np.exp(-(((self._gx - face.cx) / (face.width * 0.8)) ** 4))
        above = 1.0 - _smoothstep((self._gy - chin) / max(shoulders - chin, 1.0))
        self._w_head = (across * above).astype(np.float32)
        self._center = (width / 2.0, height / 2.0)

    def _person_weight(self, face: FaceAnchor, matte: FloatArray | None) -> FloatArray:
        gw, gh = self._grid_size
        if matte is not None:
            soft = cv2.GaussianBlur(matte.astype(np.float32), (0, 0), 12.0)
            small = cv2.resize(soft, (gw, gh), interpolation=cv2.INTER_AREA)
            return np.clip(np.asarray(small, dtype=np.float32), 0.0, 1.0)
        # No matte: a broad region around and below the face.
        across = np.exp(-(((self._gx - face.cx) / (face.width * 1.9)) ** 2))
        below = _smoothstep((self._gy - (face.cy - face.height * 1.2)) / face.height)
        return (across * below).astype(np.float32)

    @property
    def nod(self) -> float:
        """Current head dip in pixels (positive is down)."""
        return self._nod_pos

    @property
    def activity(self) -> float:
        """0 when idle, easing toward 1 while talking."""
        return self._activity

    def step(self, talking: bool, loud_db: float | None = None) -> None:
        """Advance one frame. ``loud_db`` is the level of speech just ahead."""
        dt = self._dt
        self._t += dt
        alpha = 1.0 - math.exp(-dt / _ACTIVITY_TAU_S)
        self._activity += ((1.0 if talking else 0.0) - self._activity) * alpha
        rate = _BREATH_HZ[0] + (_BREATH_HZ[1] - _BREATH_HZ[0]) * self._activity
        self._breath_phase = (self._breath_phase + rate * dt) % 1.0

        strength = self._accents.update(loud_db)
        if strength is not None:
            self._nods.append((self._t, self._nod_peak * strength))

        self._nods = [(s, a) for s, a in self._nods if self._t - s < _NOD_SECONDS]
        total = sum(a * nod_shape((self._t - s) / _NOD_SECONDS) for s, a in self._nods)
        self._nod_pos = min(total, self._nod_peak)

    def _breath(self) -> float:
        """0..1, a quicker inhale (40 %) and a slower exhale."""
        p = self._breath_phase
        if p < 0.4:
            return 0.5 - 0.5 * math.cos(math.pi * p / 0.4)
        return 0.5 + 0.5 * math.cos(math.pi * (p - 0.4) / 0.6)

    def displacement(self) -> tuple[FloatArray, FloatArray]:
        """Content displacement on the coarse grid: where each point moves to."""
        h = self._height
        t = self._t
        act = self._activity
        vx = np.zeros_like(self._gx)
        vy = -_BREATH_RISE * h * self._breath() * self._w_breath

        sway_amp = (_SWAY[0] + (_SWAY[1] - _SWAY[0]) * act) * h
        vx = vx + sway_amp * _drift(t, ((7.3, 0.0), (11.9, 1.7), (4.6, 4.1))) * self._w_sway
        vy = vy + 0.25 * sway_amp * _drift(t, ((6.1, 2.2), (9.7, 0.4))) * self._w_sway

        lean = _LEAN_SCALE * act
        vx = vx + lean * self._lean_dx
        vy = vy + lean * self._lean_dy
        vy = vy + self._nod_pos * self._w_head
        if not self._camera:
            return vx.astype(np.float32), vy.astype(np.float32)

        # Camera: a similarity transform about the frame center, on everything.
        cx, cy = self._center
        zoom = _CAMERA_ZOOM + _CAMERA_ZOOM_DRIFT * _drift(t, ((13.7, 0.9), (8.3, 2.6)))
        roll = math.radians(_CAMERA_ROLL_DEG) * _drift(t, ((9.1, 1.3), (5.3, 0.2)))
        shift_x = _CAMERA_SHIFT * h * _drift(t, ((5.9, 0.7), (8.9, 3.3), (3.7, 1.9)))
        shift_y = _CAMERA_SHIFT * h * _drift(t, ((6.7, 2.9), (10.3, 0.5), (4.3, 4.4)))
        px = self._gx + vx - cx
        py = self._gy + vy - cy
        cos, sin = math.cos(roll) * zoom, math.sin(roll) * zoom
        out_x = cos * px - sin * py + cx + shift_x
        out_y = sin * px + cos * py + cy + shift_y
        return (out_x - self._gx).astype(np.float32), (out_y - self._gy).astype(np.float32)

    def warp(self, frame: ImageArray) -> ImageArray:
        """Apply the current displacement to a full frame (BGR or RGB)."""
        if not self.enabled:
            return frame
        vx, vy = self.displacement()
        size = (self._width, self._height)
        # Content moving by v means each output pixel samples from p - v. The
        # displacement is smooth, so inverting it pointwise is accurate enough.
        dx = cv2.resize(vx, size, interpolation=cv2.INTER_LINEAR)
        dy = cv2.resize(vy, size, interpolation=cv2.INTER_LINEAR)
        map_x = self._id_x - dx
        map_y = self._id_y - dy
        out = cv2.remap(
            frame, map_x, map_y, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
        )
        return np.asarray(out, dtype=np.uint8)
