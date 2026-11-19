"""Streaming mel spectrogram matching Wav2Lip's training front end.

Wav2Lip was trained on librosa mels with these exact parameters (its
``hparams.py``): 16 kHz audio, pre-emphasis 0.97, STFT 800/200 with a Hann
window, 80 slaney mel bands from 55 Hz to 7600 Hz, dB scaled against a 20 dB
reference and squashed into [-4, 4]. The lip shapes depend on reproducing that
front end, so it is reimplemented here in numpy instead of pulling in librosa.

One mel frame covers 12.5 ms (80 frames per second). The model eats 16 mel
frames (200 ms) per video frame, starting at the video frame's timestamp.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

SAMPLE_RATE = 16000
N_FFT = 800
HOP = 200
N_MELS = 80
MEL_STEP = 16
"""Mel frames consumed per video frame (200 ms)."""
MEL_FPS = SAMPLE_RATE / HOP

_FMIN = 55.0
_FMAX = 7600.0
_PREEMPHASIS = 0.97
_REF_LEVEL_DB = 20.0
_MIN_LEVEL_DB = -100.0
_MAX_ABS = 4.0

FloatArray = npt.NDArray[np.float32]


def _hz_to_mel(freq: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    f_sp = 200.0 / 3
    min_log_hz = 1000.0
    min_log_mel = min_log_hz / f_sp
    logstep = np.log(6.4) / 27.0
    mels = freq / f_sp
    log_region = freq >= min_log_hz
    mels[log_region] = min_log_mel + np.log(freq[log_region] / min_log_hz) / logstep
    return mels


def _mel_to_hz(mels: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    f_sp = 200.0 / 3
    min_log_hz = 1000.0
    min_log_mel = min_log_hz / f_sp
    logstep = np.log(6.4) / 27.0
    freqs = f_sp * mels
    log_region = mels >= min_log_mel
    freqs[log_region] = min_log_hz * np.exp(logstep * (mels[log_region] - min_log_mel))
    return freqs


def _mel_filterbank() -> FloatArray:
    """Slaney-normalized triangular filterbank, shape (N_MELS, N_FFT // 2 + 1)."""
    fft_freqs = np.linspace(0.0, SAMPLE_RATE / 2, N_FFT // 2 + 1)
    edges = _hz_to_mel(np.array([_FMIN, _FMAX], dtype=np.float64))
    mel_points = _mel_to_hz(np.linspace(edges[0], edges[1], N_MELS + 2))
    diffs = np.diff(mel_points)
    ramps = mel_points[:, None] - fft_freqs[None, :]
    lower = -ramps[:-2] / diffs[:-1, None]
    upper = ramps[2:] / diffs[1:, None]
    weights = np.maximum(0.0, np.minimum(lower, upper))
    weights *= (2.0 / (mel_points[2:] - mel_points[:-2]))[:, None]
    return weights.astype(np.float32)


_FILTERBANK = _mel_filterbank()
_WINDOW = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(N_FFT) / N_FFT)).astype(np.float32)


class MelStream:
    """Incrementally turns 16 kHz mono audio into normalized mel frames.

    Mel frame ``k`` is centered on sample ``k * HOP``. Audio before the start
    of the utterance is treated as silence; :meth:`finish` pads the tail with
    silence so every video frame of the utterance can still get a full window.
    """

    def __init__(self) -> None:
        self._audio = np.zeros(N_FFT // 2, dtype=np.float32)  # leading center pad
        self._last_sample = 0.0
        self._mel = np.zeros((N_MELS, 0), dtype=np.float32)
        self._finished = False

    @property
    def frames(self) -> int:
        """Number of mel frames computed so far."""
        return int(self._mel.shape[1])

    def push(self, samples: FloatArray) -> None:
        """Append float32 samples in [-1, 1] and compute the frames they complete."""
        if samples.size == 0:
            return
        emphasized = np.empty_like(samples)
        emphasized[0] = samples[0] - _PREEMPHASIS * self._last_sample
        emphasized[1:] = samples[1:] - _PREEMPHASIS * samples[:-1]
        self._last_sample = float(samples[-1])
        self._audio = np.concatenate([self._audio, emphasized])
        self._compute()

    def finish(self, min_frames: int) -> None:
        """Pad with silence until at least ``min_frames`` mel frames exist."""
        if self._finished and self.frames >= min_frames:
            return
        self._finished = True
        needed = (max(min_frames, self.frames + 1) - 1) * HOP + N_FFT
        if needed > self._audio.size:
            pad = np.zeros(needed - self._audio.size, dtype=np.float32)
            self._audio = np.concatenate([self._audio, pad])
        self._compute()

    def window(self, start: int) -> FloatArray | None:
        """Return mel frames ``[start, start + MEL_STEP)`` or None if not ready."""
        if start + MEL_STEP > self.frames:
            return None
        return self._mel[:, start : start + MEL_STEP]

    def _compute(self) -> None:
        done = self.frames
        total = (self._audio.size - N_FFT) // HOP + 1
        if total <= done:
            return
        idx = np.arange(done, total)[:, None] * HOP + np.arange(N_FFT)[None, :]
        spectrum = np.abs(np.fft.rfft(self._audio[idx] * _WINDOW, axis=1)).astype(np.float32)
        mel = _FILTERBANK @ spectrum.T
        db = 20.0 * np.log10(np.maximum(1e-5, mel)) - _REF_LEVEL_DB
        norm = np.clip(
            (2 * _MAX_ABS) * ((db - _MIN_LEVEL_DB) / -_MIN_LEVEL_DB) - _MAX_ABS,
            -_MAX_ABS,
            _MAX_ABS,
        )
        self._mel = np.concatenate([self._mel, norm.astype(np.float32)], axis=1)
