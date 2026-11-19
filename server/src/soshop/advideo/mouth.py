"""Mouth opening from speech audio, one value per video frame.

The opening of the mouth tracks the first formant: open vowels ("a", "o")
put strong energy between roughly 500 and 1300 Hz, close vowels ("i", "u")
and the nasals that end so many Vietnamese syllables ("m", "n", "ng") put
little there. So the level of that band, normalized over the clip, is a
usable stand-in for jaw opening without any phoneme model.

Two shaping steps make it look like articulation rather than a volume meter:
part of a 250 ms running mean is subtracted so the mouth closes between
syllables of continuous speech, and the result opens fast and closes a
little slower. Each frame is sampled 40 ms ahead of its own time because
the mouth moves just before the sound it makes.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]

_HOP_S = 0.01
_BAND_HZ = (500.0, 1300.0)
_CONTRAST = 0.45
_CONTRAST_WINDOW = 25  # hops, 250 ms
_ATTACK = 0.7
_RELEASE = 0.45
_LEAD_S = 0.04


def _band_levels(samples: FloatArray, rate: int) -> FloatArray:
    hop = int(rate * _HOP_S)
    win = 3 * hop
    count = max(1, len(samples) // hop)
    freqs = np.fft.rfftfreq(win, 1 / rate)
    band = (freqs > _BAND_HZ[0]) & (freqs < _BAND_HZ[1])
    window = np.hanning(win)
    padded = np.pad(samples, (win // 2, win))
    frames = np.lib.stride_tricks.sliding_window_view(padded, win)[::hop][:count]
    spectrum = np.abs(np.fft.rfft(frames * window, axis=1)) ** 2
    return np.asarray(10 * np.log10(spectrum[:, band].sum(axis=1) + 1e-9), dtype=np.float64)


def _normalize(levels: FloatArray) -> FloatArray:
    voiced = levels > np.percentile(levels, 20) + 10
    low = np.percentile(levels[voiced], 15) if voiced.any() else levels.min()
    high = np.percentile(levels, 97)
    return np.clip((levels - low) / max(high - low, 1e-3), 0, 1)


def _contrast(level: FloatArray) -> FloatArray:
    k = _CONTRAST_WINDOW
    padded = np.pad(level, (k // 2, k - k // 2 - 1), mode="edge")
    slow = np.convolve(padded, np.ones(k) / k, mode="valid")
    # The small lift keeps sustained vowels open; silence must stay at zero.
    lift = np.where(level > 0.02, 0.2 * slow.mean(), 0.0)
    shaped = np.clip(level - _CONTRAST * slow + lift, 0, 1)
    peak = np.percentile(shaped, 97)
    return np.asarray(np.clip(shaped / max(peak, 1e-3), 0, 1), dtype=np.float64)


def _envelope(level: FloatArray) -> FloatArray:
    out = np.zeros_like(level)
    for i in range(1, len(level)):
        rate = _ATTACK if level[i] > out[i - 1] else _RELEASE
        out[i] = out[i - 1] + rate * (level[i] - out[i - 1])
    return out


def openness(samples: npt.NDArray[np.int16] | FloatArray, rate: int, fps: int) -> FloatArray:
    """Mouth opening in ``[0, 1]`` for each video frame of ``samples``."""
    audio = np.asarray(samples, dtype=np.float64)
    if np.issubdtype(np.asarray(samples).dtype, np.integer):
        audio = audio / 32768.0
    frames = int(len(audio) * fps // rate)
    if frames == 0 or not np.any(audio):
        return np.zeros(frames)
    curve = _envelope(_contrast(_normalize(_band_levels(audio, rate))))
    hops_per_frame = 1.0 / (fps * _HOP_S)
    centers = (np.arange(frames) + 0.5) * hops_per_frame + _LEAD_S / _HOP_S
    index = np.clip(np.round(centers).astype(int), 0, len(curve) - 1)
    return curve[index]
