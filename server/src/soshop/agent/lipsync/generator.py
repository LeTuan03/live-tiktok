"""Realtime video generator: the motion clip with a lip-synced mouth.

Plugs into livekit-agents' ``AvatarRunner`` as its ``VideoGenerator``. The
runner feeds it the agent's TTS audio and forwards what it yields to an
``AVSynchronizer``, which paces video at the clip's frame rate and keeps audio
aligned with it.

While there is speech, each video frame is yielded together with exactly one
frame-duration of audio, which is what keeps lips and sound in sync. A video
frame can only be rendered once 200 ms of audio from its timestamp onward has
arrived (the model's receptive field), so speech starts about a fifth of a
second after the first TTS audio. With no speech the clip simply plays, and no
inference runs at all.

Behavior on top of the lip sync:

* the clip has an idle and a talk track (``motion.py``). Playback follows the
  talk track while there is speech and the idle track otherwise, switching
  only at portal frames where both show the same pose, so there is no jump;
* every finished frame goes through ``performance.Performer``: breathing,
  body sway, a lean toward the camera while talking, nods on stressed
  syllables, and a slow camera drift.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import cv2
import numpy as np
import numpy.typing as npt
from livekit import rtc
from livekit.agents.voice.avatar import AudioSegmentEnd, VideoGenerator

from soshop.agent.lipsync import align
from soshop.agent.lipsync.assets import MotionClip
from soshop.agent.lipsync.mel import MEL_FPS, MEL_STEP, SAMPLE_RATE, MelStream
from soshop.agent.lipsync.motion import IDLE, TALK
from soshop.agent.lipsync.performance import FaceAnchor, Performer, loudness
from soshop.agent.lipsync.wav2lip import FACE_SIZE, Wav2LipEngine

logger = logging.getLogger("soshop.agent.lipsync")

FloatArray = npt.NDArray[np.float32]
ImageArray = npt.NDArray[np.uint8]

_BATCH = 3
"""Frames rendered per worker-thread hop."""
_RAMP_FRAMES = 3
"""Frames over which the synced mouth fades in and out of the clip's own."""
_SHARPEN = 0.5
_MAX_STRIDE = 3
"""Under load the mouth is re-inferred only on every 2nd or 3rd frame and the
previous mouth is reused in between. Inference is most of a frame's cost, so
this cuts it by half or more while the head, eyes and audio keep the full
frame rate. Falling behind realtime is worse than a coarser mouth: the audio
is paced by the video, so slow frames stretch every sentence."""
_SLOW_RATIO = 0.85
_FAST_RATIO = 0.4
"""Smoothed render time as a fraction of the frame budget above which the
stride is raised, and below which it is lowered again."""
_STRIDE_UP_DWELL_S = 1.0
_STRIDE_DOWN_DWELL_S = 20.0
"""Minimum time at a stride before raising or lowering it. Raising is quick
because being slow is audible; lowering is slow so the stride does not flap
between a setting that keeps up and one that does not."""

_IDLE_AFTER_S = 0.8
"""Silence after which the host counts as idle again. Bridges the short gaps
between sentences so the body does not flip between talking and listening."""
_NOD_LOOKAHEAD = (1, 3)
"""Frames ahead of the current one whose loudness drives the beat nods. A nod
takes about 160 ms to reach its lowest point, so starting it 40 to 120 ms
before the syllable puts the dip on the syllable."""

Output = rtc.VideoFrame | rtc.AudioFrame | AudioSegmentEnd


_MAX_TONE_SHIFT = 20.0
"""Largest per-channel correction (0 to 255 scale) applied to make the model's
skin tone match the frame's. Real mismatches are a few levels."""

# Blend mask in aligned-face pixels as (center x, center y, half width, half
# height) of a feathered ellipse over mouth, jaw and lower cheeks. The first
# is for clips with per-frame alignment, the second for older clips that only
# have a face box, whose taller box puts the mouth lower.
_ALIGNED_MASK = (48.0, 66.0, 27.0, 21.0)
_BOX_MASK = (48.0, 73.9, 34.6, 24.0)

# The model paints teeth the color of skin, a dull orange that reads as fake
# at once. Inside the inner mouth (aligned clips: an ellipse between the
# lips), pixels much brighter than the closed-mouth reference are new teeth
# and get their color pulled toward a neutral white. Skin and lips, which
# match the reference, are left alone.
_INNER_MOUTH = (48, 63, 15, 5)
_TEETH_MIN_LIFT = 8.0
_TEETH_RAMP = 20.0
_TEETH_DESATURATE = (0.6, 0.7)
"""Share of the red-green and blue-yellow chroma removed from full teeth."""
_TEETH_BRIGHTEN = 5.0


def _inner_mouth_mask() -> FloatArray:
    cx, cy, half_w, half_h = _INNER_MOUTH
    mask = np.zeros((FACE_SIZE, FACE_SIZE), dtype=np.float32)
    cv2.ellipse(mask, (cx, cy), (half_w, half_h), 0, 0, 360, 1.0, -1)
    return np.asarray(cv2.GaussianBlur(mask, (0, 0), 1.5), dtype=np.float32)


def _whiten_teeth(synced: FloatArray, reference: ImageArray, inner: FloatArray) -> FloatArray:
    """Neutralize the color of teeth the model drew into an opened mouth."""
    synced_u8 = np.clip(synced, 0, 255).astype(np.uint8)
    lab = cv2.cvtColor(synced_u8, cv2.COLOR_BGR2LAB).astype(np.float32)
    ref_light = cv2.cvtColor(reference, cv2.COLOR_BGR2LAB)[:, :, 0].astype(np.float32)
    teeth = np.clip((lab[:, :, 0] - ref_light - _TEETH_MIN_LIFT) / _TEETH_RAMP, 0, 1) * inner
    if float(teeth.max()) <= 0.0:
        return synced
    lab[:, :, 1] = 128 + (lab[:, :, 1] - 128) * (1 - _TEETH_DESATURATE[0] * teeth)
    lab[:, :, 2] = 128 + (lab[:, :, 2] - 128) * (1 - _TEETH_DESATURATE[1] * teeth)
    lab[:, :, 0] += _TEETH_BRIGHTEN * teeth
    out = cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)
    return np.asarray(out, dtype=np.float32)


def _mouth_mask(shape: tuple[float, float, float, float]) -> FloatArray:
    """Blend weight of the repainted mouth over the aligned face, ``(96, 96)``.

    Forced to zero at the border so the paste never shows an edge.
    """
    cx, cy, half_w, half_h = shape
    mask = np.zeros((FACE_SIZE, FACE_SIZE), dtype=np.float32)
    cv2.ellipse(mask, (round(cx), round(cy)), (round(half_w), round(half_h)), 0, 0, 360, 1.0, -1)
    mask = np.asarray(cv2.GaussianBlur(mask, (0, 0), 5.0), dtype=np.float32)
    margin = 6
    edge = np.minimum(np.arange(FACE_SIZE), FACE_SIZE - 1 - np.arange(FACE_SIZE))
    ramp = np.clip(edge / margin, 0, 1)
    weights: FloatArray = (mask * ramp[None, :] * ramp[:, None]).astype(np.float32)
    return weights


@dataclass(frozen=True)
class _Patch:
    """A lip-synced mouth ready to blend into a frame region."""

    x0: int
    y0: int
    x1: int
    y1: int
    pixels: ImageArray
    alpha: FloatArray
    """``(h, w)`` blend weight of ``pixels`` over the frame."""
    rest: FloatArray
    """``1 - alpha``, precomputed: the patch is blended on several frames."""


@dataclass
class _Utterance:
    """One TTS segment: its audio, mel features and render progress."""

    rate: int
    samples_per_frame: int
    pcm: bytearray = field(default_factory=bytearray)
    mel: MelStream = field(default_factory=MelStream)
    total_samples: int = 0
    emitted: int = 0
    ended: bool = False
    _to_mel: rtc.AudioResampler | None = None
    _to_rate: dict[int, rtc.AudioResampler] = field(default_factory=dict)

    def add(self, frame: rtc.AudioFrame) -> None:
        samples = np.frombuffer(frame.data, dtype=np.int16)
        if frame.num_channels > 1:
            samples = samples.reshape(-1, frame.num_channels).mean(axis=1).astype(np.int16)
        chunks = [samples.tobytes()]
        if frame.sample_rate != self.rate:
            resampler = self._to_rate.setdefault(
                frame.sample_rate, rtc.AudioResampler(frame.sample_rate, self.rate)
            )
            chunks = [bytes(f.data) for f in resampler.push(bytearray(chunks[0]))]
        for chunk in chunks:
            self.pcm.extend(chunk)
            self.total_samples += len(chunk) // 2
            if self._to_mel is None:
                self._to_mel = rtc.AudioResampler(self.rate, SAMPLE_RATE)
            self._push_mel(self._to_mel.push(bytearray(chunk)))

    def end(self) -> None:
        self.ended = True
        if self._to_mel is not None:
            self._push_mel(self._to_mel.flush())

    def _push_mel(self, frames: list[rtc.AudioFrame]) -> None:
        for frame in frames:
            data = np.frombuffer(frame.data, dtype=np.int16).astype(np.float32) / 32768.0
            self.mel.push(data)

    @property
    def total_frames(self) -> int:
        return math.ceil(self.total_samples / self.samples_per_frame)

    @property
    def finished(self) -> bool:
        return self.ended and self.emitted >= self.total_frames

    def mel_start(self, index: int, fps: int) -> int:
        return int(index * MEL_FPS / fps)

    def renderable(self, fps: int) -> int:
        """How many frames from ``emitted`` onward can be rendered right now."""
        if self.ended:
            return self.total_frames - self.emitted
        count = self.total_samples // self.samples_per_frame
        while count > self.emitted and (
            self.mel_start(count - 1, fps) + MEL_STEP > self.mel.frames
        ):
            count -= 1
        return count - self.emitted

    def upcoming_loudness(self, first: int, last: int) -> float | None:
        """Level (dBFS) of the buffered audio ``first`` to ``last`` frames ahead."""
        size = self.samples_per_frame * 2
        chunk = bytes(self.pcm[first * size : last * size])
        return loudness(chunk) if chunk else None

    def take_audio(self) -> bytes:
        """Pop one video frame's worth of audio, zero-padded at the very end."""
        size = self.samples_per_frame * 2
        chunk = bytes(self.pcm[:size])
        del self.pcm[:size]
        return chunk + b"\x00" * (size - len(chunk))


class LipSyncVideoGenerator(VideoGenerator):
    def __init__(
        self,
        clip: MotionClip,
        engine: Wav2LipEngine,
        *,
        sample_rate: int,
        matte: FloatArray | None = None,
        perform: bool = True,
    ) -> None:
        if sample_rate % clip.fps:
            raise ValueError(f"sample rate {sample_rate} is not a multiple of {clip.fps} fps")
        self._clip = clip
        self._engine = engine
        self._rate = sample_rate
        self._samples_per_frame = sample_rate // clip.fps
        self._queue: asyncio.Queue[rtc.AudioFrame | AudioSegmentEnd] = asyncio.Queue()
        self._pending: deque[_Utterance] = deque()
        self._epoch = 0
        self._cursor = 0
        self._n_tracks = int(getattr(clip, "n_tracks", 1))
        self._portals = np.asarray(getattr(clip, "portals", np.ones(len(clip), dtype=bool)))
        self._track = IDLE
        self._idle_after = round(_IDLE_AFTER_S * clip.fps)
        self._quiet_frames = self._idle_after  # starts idle, not "just talked"
        # Aligned-face transform per track and frame, flattened to one row per
        # (track, index). Clips built before alignment existed only carry a
        # face box, which is the unrotated special case.
        aligned = getattr(clip, "align", None)
        self._inner_mouth: FloatArray | None = None
        if aligned is None:
            boxes = np.stack([align.box_to_frame(box, FACE_SIZE) for box in clip.faces])
            to_frame = np.tile(boxes, (self._n_tracks, 1, 1))
            self._mask = _mouth_mask(_BOX_MASK)
        else:
            to_frame = np.asarray(aligned, dtype=np.float32).reshape(-1, 2, 3)
            self._mask = _mouth_mask(_ALIGNED_MASK)
            self._inner_mouth = _inner_mouth_mask()
        self._to_frame = to_frame
        self._to_face = np.stack([align.invert(m) for m in to_frame])
        self._rois = [align.bounds(m, FACE_SIZE, clip.width, clip.height) for m in to_frame]
        # Skin around the mouth, where the model's output should match the frame.
        self._tone_ring = (self._mask > 0.05) & (self._mask < 0.6)
        self._slow_logged_at = 0.0
        self._stride = 1
        self._frame_ms = 0.0
        self._stride_changed_at = time.monotonic()
        self._patch: _Patch | None = None
        self._performer: Performer | None = None
        if perform:
            self._performer = Performer(
                clip.width, clip.height, clip.fps, FaceAnchor.from_boxes(clip.faces), matte
            )

    @property
    def track(self) -> int:
        """Behavior track currently playing (``motion.IDLE`` or ``motion.TALK``)."""
        return self._track

    @property
    def talking(self) -> bool:
        """Speech is queued, or ended less than ``_IDLE_AFTER_S`` ago."""
        return bool(self._pending) or self._quiet_frames < self._idle_after

    async def push_audio(self, frame: rtc.AudioFrame | AudioSegmentEnd) -> None:
        await self._queue.put(frame)

    def clear_buffer(self) -> None:
        """Drop all queued speech (interruption). In-flight renders are discarded."""
        while not self._queue.empty():
            self._queue.get_nowait()
        self._pending.clear()
        self._epoch += 1
        self._patch = None

    def __aiter__(self) -> AsyncIterator[Output]:
        return self._stream()

    def _drain(self) -> None:
        while not self._queue.empty():
            item = self._queue.get_nowait()
            last = self._pending[-1] if self._pending else None
            if isinstance(item, AudioSegmentEnd):
                if last is not None and not last.ended:
                    last.end()
                continue
            if last is None or last.ended:
                last = _Utterance(rate=self._rate, samples_per_frame=self._samples_per_frame)
                self._pending.append(last)
            last.add(item)

    async def _stream(self) -> AsyncIterator[Output]:
        while True:
            self._drain()
            utterance = self._pending[0] if self._pending else None
            count = utterance.renderable(self._clip.fps) if utterance is not None else 0
            if utterance is None or (count == 0 and not utterance.finished):
                # Nothing to say (or still buffering lookahead): keep the clip moving.
                # The synchronizer's bounded queue paces this loop at the frame rate.
                yield await asyncio.to_thread(self._render_idle)
                continue

            epoch = self._epoch
            if count > 0:
                rendered = await asyncio.to_thread(
                    self._render_speech, utterance, min(count, _BATCH)
                )
                if epoch != self._epoch:
                    continue  # interrupted while rendering
                for video, audio in rendered:
                    yield video
                    yield audio
            if utterance.finished and epoch == self._epoch:
                self._pending.popleft()
                yield AudioSegmentEnd()

    def _next_frame(self) -> tuple[ImageArray, int]:
        """Decode the next timeline frame. Returns it with its transform row."""
        self._quiet_frames = 0 if self._pending else self._quiet_frames + 1
        index = self._cursor
        self._cursor = (self._cursor + 1) % len(self._clip)
        want = TALK if self.talking and self._n_tracks > TALK else IDLE
        if want != self._track and self._portals[index]:
            self._track = want
        frame = self._clip.frame(index, self._track)
        return frame, self._track * len(self._clip) + index

    def _to_video(self, frame: ImageArray, loud_db: float | None = None) -> rtc.VideoFrame:
        if self._performer is not None:
            self._performer.step(self.talking, loud_db)
            frame = self._performer.warp(frame)
        rgba = cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA)
        return rtc.VideoFrame(
            self._clip.width, self._clip.height, rtc.VideoBufferType.RGBA, rgba.tobytes()
        )

    def _render_idle(self) -> rtc.VideoFrame:
        frame, _ = self._next_frame()
        return self._to_video(frame)

    def _render_speech(
        self, utterance: _Utterance, count: int
    ) -> list[tuple[rtc.VideoFrame, rtc.AudioFrame]]:
        started = time.perf_counter()
        fps = self._clip.fps
        out: list[tuple[rtc.VideoFrame, rtc.AudioFrame]] = []
        for _ in range(count):
            index = utterance.emitted
            start = utterance.mel_start(index, fps)
            if utterance.ended:
                utterance.mel.finish(start + MEL_STEP)
            mel = utterance.mel.window(start)
            frame, clip_index = self._next_frame()
            if mel is not None:
                weight = min(1.0, (index + 1) / _RAMP_FRAMES)
                if utterance.ended:
                    weight = min(weight, (utterance.total_frames - index) / _RAMP_FRAMES)
                # The first frame of an utterance always gets a fresh mouth:
                # a patch left over from the previous sentence would be wrong.
                reuse = index > 0 and index % self._stride != 0
                self._sync_mouth(frame, clip_index, mel, weight, reuse=reuse)
            loud = utterance.upcoming_loudness(*_NOD_LOOKAHEAD)
            audio = rtc.AudioFrame(
                data=utterance.take_audio(),
                sample_rate=self._rate,
                num_channels=1,
                samples_per_channel=self._samples_per_frame,
            )
            utterance.emitted += 1
            out.append((self._to_video(frame, loud), audio))
        self._note_speed(time.perf_counter() - started, count)
        return out

    def _sync_mouth(
        self,
        frame: ImageArray,
        clip_index: int,
        mel: FloatArray,
        weight: float,
        *,
        reuse: bool = False,
    ) -> None:
        patch = self._patch if reuse else None
        if patch is None:
            patch = self._infer_patch(frame, clip_index, mel)
            self._patch = patch
        region = frame[patch.y0 : patch.y1, patch.x0 : patch.x1]
        alpha, rest = patch.alpha, patch.rest
        if weight < 1.0:
            alpha = alpha * np.float32(weight)
            rest = 1.0 - alpha
        # One fused OpenCV pass; the numpy version spent ~6 ms on temporaries.
        region[:] = cv2.blendLinear(patch.pixels, region, alpha, rest)

    def _infer_patch(self, frame: ImageArray, clip_index: int, mel: FloatArray) -> _Patch:
        """Lip-sync the upright face of this frame and map it back onto the frame."""
        warped = cv2.warpAffine(
            frame, self._to_face[clip_index], (FACE_SIZE, FACE_SIZE), flags=cv2.INTER_LINEAR
        )
        face: ImageArray = np.asarray(warped, dtype=np.uint8)
        synced = self._engine.infer(mel, face).astype(np.float32)
        # The model shifts skin tone slightly; pull it back so the blend is invisible.
        tone = face[self._tone_ring].mean(axis=0) - synced[self._tone_ring].mean(axis=0)
        synced += np.clip(tone, -_MAX_TONE_SHIFT, _MAX_TONE_SHIFT)
        if self._inner_mouth is not None:
            synced = _whiten_teeth(synced, face, self._inner_mouth)

        x0, y0, x1, y1 = self._rois[clip_index]
        to_roi = self._to_frame[clip_index].copy()
        to_roi[:, 2] -= (x0, y0)
        size = (x1 - x0, y1 - y0)
        pixels = cv2.warpAffine(synced, to_roi, size, flags=cv2.INTER_CUBIC)
        if _SHARPEN > 0:
            blurred = cv2.GaussianBlur(pixels, (0, 0), 1.6)
            pixels = pixels + _SHARPEN * (pixels - blurred)
        alpha = np.asarray(
            cv2.warpAffine(self._mask, to_roi, size, flags=cv2.INTER_LINEAR), dtype=np.float32
        )
        return _Patch(
            x0,
            y0,
            x1,
            y1,
            np.clip(pixels, 0, 255).astype(np.uint8),
            alpha,
            (1.0 - alpha).astype(np.float32),
        )

    def _note_speed(self, elapsed: float, count: int) -> None:
        """Adapt the inference stride to how long frames actually take."""
        budget_ms = 1000 / self._clip.fps
        frame_ms = elapsed / count * 1000
        self._frame_ms = frame_ms if self._frame_ms == 0 else 0.8 * self._frame_ms + 0.2 * frame_ms
        now = time.monotonic()
        dwell = now - self._stride_changed_at
        if (
            self._frame_ms > _SLOW_RATIO * budget_ms
            and self._stride < _MAX_STRIDE
            and dwell >= _STRIDE_UP_DWELL_S
        ):
            self._set_stride(self._stride + 1, now)
        elif (
            self._frame_ms < _FAST_RATIO * budget_ms
            and self._stride > 1
            and dwell >= _STRIDE_DOWN_DWELL_S
        ):
            self._set_stride(self._stride - 1, now)
        if (
            self._frame_ms > budget_ms
            and self._stride >= _MAX_STRIDE
            and dwell >= _STRIDE_UP_DWELL_S
        ):
            self._shed_performance()
        if frame_ms > budget_ms and self._stride >= _MAX_STRIDE and now - self._slow_logged_at > 10:
            self._slow_logged_at = now
            logger.warning(
                "lip sync is slower than realtime: %.0f ms per frame, budget %.0f ms",
                frame_ms,
                budget_ms,
            )

    def _set_stride(self, stride: int, now: float) -> None:
        # Carry the average over, scaled by how the per-frame cost should move,
        # so one lucky batch right after a change cannot undo it.
        self._frame_ms *= self._stride / stride
        if stride < self._stride and self._performer is not None and not self._performer.enabled:
            self._performer.enabled = True
            logger.info("lip sync: body and camera motion back on")
        self._stride = stride
        self._stride_changed_at = now
        logger.info("lip sync: mouth now updates every %d frame(s)", stride)

    def _shed_performance(self) -> None:
        """Last resort when even the coarsest mouth cannot keep up: drop the
        body and camera warp (a few ms per frame) before audio starts to drag."""
        if self._performer is not None and self._performer.enabled:
            self._performer.enabled = False
            logger.warning("lip sync: too slow even at stride %d, body motion off", self._stride)
