"""Face alignment for the lip-sync model.

Wav2Lip was trained on upright, tightly cropped faces. A portrait with the
head tilted (very common in posed photos) fed as an axis-aligned box comes
back with smeared teeth and a mouth drawn at the wrong angle. So each frame
gets an affine transform that maps a small upright square, the *aligned
face*, onto the tilted face in the frame. The realtime generator samples the
face through it, runs the model on the upright crop, and pastes the result
back through the same transform.

The aligned square is laid out from two robust anchors, the midpoint between
the eyes and the center of the mouth:

* its vertical axis follows the head roll (perpendicular to the eye line);
* its size is a fixed multiple of the eye-to-mouth distance, constant over
  the clip so the mouth never "breathes";
* the mouth sits about two thirds of the way down, inside the lower half the
  model repaints.

Everything here is plain numpy so it can be unit-tested without OpenCV.
"""

from __future__ import annotations

import math

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float32]

# 106-landmark indices (InsightFace 2d106det layout).
_LEFT_EYE = (33, 35, 40, 39)
_RIGHT_EYE = (87, 89, 94, 93)
_MOUTH = slice(52, 72)

# Aligned square, in units of the eye-to-mouth distance.
_HALF_WIDTH = 1.25
_ABOVE = 1.45
_BELOW = 1.35
# Where along the eye-to-mouth line the square is centered.
_CENTER_ALONG = 0.62

_SMOOTH_WINDOW = 7


def anchors(landmarks: FloatArray) -> tuple[FloatArray, FloatArray, float]:
    """Eye midpoint, mouth center and head roll (radians) from 106 landmarks."""
    left = landmarks[list(_LEFT_EYE)].mean(axis=0)
    right = landmarks[list(_RIGHT_EYE)].mean(axis=0)
    if left[0] > right[0]:
        left, right = right, left
    roll = math.atan2(float(right[1] - left[1]), float(right[0] - left[0]))
    eyes: FloatArray = ((left + right) / 2).astype(np.float32)
    mouth: FloatArray = landmarks[_MOUTH].mean(axis=0).astype(np.float32)
    return eyes, mouth, roll


def face_to_frame(
    eyes: FloatArray, mouth: FloatArray, roll: float, eye_mouth: float, size: int
) -> FloatArray:
    """Affine ``(2, 3)`` mapping aligned-face pixels to frame pixels."""
    right = np.array([math.cos(roll), math.sin(roll)], dtype=np.float32)
    down = np.array([-math.sin(roll), math.cos(roll)], dtype=np.float32)
    center = eyes + (mouth - eyes) * _CENTER_ALONG
    top_left = center - right * (_HALF_WIDTH * eye_mouth) - down * (_ABOVE * eye_mouth)
    step_x = right * (2 * _HALF_WIDTH * eye_mouth / size)
    step_y = down * ((_ABOVE + _BELOW) * eye_mouth / size)
    return np.array(
        [[step_x[0], step_y[0], top_left[0]], [step_x[1], step_y[1], top_left[1]]],
        dtype=np.float32,
    )


def box_to_frame(box: npt.NDArray[np.int32], size: int) -> FloatArray:
    """The same mapping for a plain axis-aligned box (clips built without alignment)."""
    x0, y0, x1, y1 = (float(v) for v in box)
    return np.array(
        [[(x1 - x0) / size, 0.0, x0], [0.0, (y1 - y0) / size, y0]],
        dtype=np.float32,
    )


def _smooth_loop(values: npt.NDArray[np.float64], window: int) -> npt.NDArray[np.float64]:
    """Moving average over a looping sequence, along axis 0."""
    if len(values) < window:
        return values
    pad = window // 2
    wrapped = np.concatenate([values[-pad:], values, values[:pad]])
    kernel = np.ones(window) / window
    if wrapped.ndim == 1:
        return np.convolve(wrapped, kernel, mode="valid")
    return np.stack(
        [np.convolve(wrapped[:, k], kernel, mode="valid") for k in range(wrapped.shape[1])], axis=1
    )


def eye_mouth_distance(landmarks_per_frame: list[FloatArray]) -> float:
    """Median eye-to-mouth distance over a set of frames, in pixels."""
    found = [anchors(lm) for lm in landmarks_per_frame]
    eyes = np.array([a[0] for a in found], dtype=np.float64)
    mouths = np.array([a[1] for a in found], dtype=np.float64)
    return float(np.median(np.linalg.norm(mouths - eyes, axis=1)))


def clip_transforms(
    landmarks_per_frame: list[FloatArray], size: int, eye_mouth: float | None = None
) -> FloatArray:
    """Per-frame aligned-face transforms for a looping clip, shape ``(frames, 2, 3)``.

    Landmark detection jitters by a pixel or so from frame to frame, which
    would make the pasted mouth shimmer, so the anchors are smoothed around
    the loop and the square keeps one size for the whole clip. Pass
    ``eye_mouth`` to share one size across several tracks of the same clip,
    so the mouth does not change scale when playback switches track.
    """
    found = [anchors(lm) for lm in landmarks_per_frame]
    eyes = _smooth_loop(np.array([a[0] for a in found], dtype=np.float64), _SMOOTH_WINDOW)
    mouths = _smooth_loop(np.array([a[1] for a in found], dtype=np.float64), _SMOOTH_WINDOW)
    rolls = _smooth_loop(np.array([a[2] for a in found], dtype=np.float64), _SMOOTH_WINDOW)
    if eye_mouth is None:
        eye_mouth = float(np.median(np.linalg.norm(mouths - eyes, axis=1)))
    return np.stack(
        [
            face_to_frame(
                eyes[i].astype(np.float32),
                mouths[i].astype(np.float32),
                float(rolls[i]),
                eye_mouth,
                size,
            )
            for i in range(len(found))
        ]
    )


def invert(matrix: FloatArray) -> FloatArray:
    """Inverse of a ``(2, 3)`` affine transform."""
    linear = np.linalg.inv(matrix[:, :2].astype(np.float64))
    offset = -linear @ matrix[:, 2].astype(np.float64)
    return np.concatenate([linear, offset[:, None]], axis=1).astype(np.float32)


def bounds(matrix: FloatArray, size: int, width: int, height: int) -> tuple[int, int, int, int]:
    """Frame-space bounding box ``x0, y0, x1, y1`` of the aligned square, clamped."""
    corners = np.array([[0, 0, 1], [size, 0, 1], [0, size, 1], [size, size, 1]], dtype=np.float32)
    points = corners @ matrix.T
    x0 = max(0, math.floor(float(points[:, 0].min())))
    y0 = max(0, math.floor(float(points[:, 1].min())))
    x1 = min(width, math.ceil(float(points[:, 0].max())))
    y1 = min(height, math.ceil(float(points[:, 1].max())))
    return x0, y0, x1, y1
