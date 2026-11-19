"""Hermetic tests for the local lip-sync avatar (no model weights, no network).

The models themselves are exercised by the build CLI and the room test
described in ``soshop/agent/lipsync/README.md``. These tests cover the logic
around them: the mel front end, the seamless motion loop, the asset status
contract the API polls, and the generator's audio/video pairing.
"""

from __future__ import annotations

import asyncio
import json

import numpy as np
import pytest

# OpenCV ships only in the `agent` dependency group.
cv2 = pytest.importorskip("cv2")

from livekit import rtc  # noqa: E402
from livekit.agents.voice.avatar import AudioSegmentEnd  # noqa: E402

from soshop.agent.lipsync import assets, avatar_assets_ready  # noqa: E402
from soshop.agent.lipsync.build import _stable_boxes  # noqa: E402
from soshop.agent.lipsync.generator import LipSyncVideoGenerator  # noqa: E402
from soshop.agent.lipsync.mel import (  # noqa: E402
    HOP,
    MEL_STEP,
    N_FFT,
    N_MELS,
    SAMPLE_RATE,
    MelStream,
)
from soshop.agent.lipsync.motion import idle_motion  # noqa: E402

RATE = 24000
FPS = 25


def test_mel_stream_frames_and_range():
    stream = MelStream()
    t = np.arange(SAMPLE_RATE, dtype=np.float32) / SAMPLE_RATE
    tone = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    stream.push(tone[:5000])
    stream.push(tone[5000:])

    # Frame k is centered on sample k * HOP, with half a window of left padding.
    assert stream.frames == (SAMPLE_RATE + N_FFT // 2 - N_FFT) // HOP + 1
    window = stream.window(0)
    assert window is not None and window.shape == (N_MELS, MEL_STEP)
    assert window.min() >= -4.0 and window.max() <= 4.0
    assert window.max() > 0.0  # a loud tone is well above the floor

    assert stream.window(stream.frames) is None
    stream.finish(stream.frames + MEL_STEP)
    tail = stream.window(stream.frames - MEL_STEP)
    assert tail is not None and tail[:, -1].max() == -4.0  # padded silence sits on the floor


def test_idle_motion_loops_seamlessly_and_blinks():
    total = 300
    first, wrapped = idle_motion(0, total, FPS), idle_motion(total, total, FPS)
    assert first.yaw == pytest.approx(wrapped.yaw, abs=1e-6)
    assert first.pitch == pytest.approx(wrapped.pitch, abs=1e-6)
    np.testing.assert_allclose(first.exp, wrapped.exp, atol=1e-6)

    # Eyelid closure shows up on keypoint 11's y delta at some point in the loop.
    lids = [idle_motion(i, total, FPS).exp[11, 1] for i in range(total)]
    assert max(lids) - min(lids) > 0.003


def test_status_contract_and_readiness(tmp_path):
    assert not avatar_assets_ready(tmp_path)
    assets.write_status(tmp_path, "processing", 0.4)
    assert assets.read_status(tmp_path) == {"status": "processing", "progress": 0.4, "error": None}
    assert not avatar_assets_ready(tmp_path)

    assets.write_status(tmp_path, "ready", 1.0)
    assert not avatar_assets_ready(tmp_path)  # status alone is not enough
    for name in (assets.FRAMES_FILE, assets.INDEX_FILE, assets.FACES_FILE):
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / assets.META_FILE).write_text(json.dumps({"version": assets.FORMAT_VERSION}))
    assert avatar_assets_ready(tmp_path)

    assets.write_status(tmp_path, "failed", 0.0, "no face found")
    assert assets.read_status(tmp_path)["error"] == "no face found"
    assert not avatar_assets_ready(tmp_path)


def test_stable_boxes_have_constant_size():
    rng = np.random.default_rng(0)
    raw = np.array([190, 80, 396, 386], dtype=np.float32) + rng.normal(0, 2, (50, 4)).astype(
        np.float32
    )
    boxes = _stable_boxes(raw)
    sizes = {(int(b[2] - b[0]), int(b[3] - b[1])) for b in boxes}
    assert len(sizes) == 1
    assert np.abs(np.diff(boxes[:, 0])).max() <= 2  # centers are smoothed


class _FakeClip:
    width, height, fps = 64, 96, FPS

    def __init__(self) -> None:
        self.faces = np.tile(np.array([16, 24, 48, 72], dtype=np.int32), (10, 1))

    def __len__(self) -> int:
        return 10

    def frame(self, index: int, track: int = 0) -> np.ndarray:
        return np.full((self.height, self.width, 3), 100, dtype=np.uint8)


class _FakeEngine:
    def __init__(self) -> None:
        self.calls = 0

    def infer(self, mel: np.ndarray, face: np.ndarray) -> np.ndarray:
        assert mel.shape == (N_MELS, MEL_STEP) and face.shape == (96, 96, 3)
        self.calls += 1
        return np.full_like(face, 250)


def _audio(seconds: float) -> list[rtc.AudioFrame]:
    samples = (np.sin(np.arange(int(RATE * seconds)) * 0.05) * 8000).astype(np.int16)
    step = RATE // 50
    return [
        rtc.AudioFrame(
            data=samples[i : i + step].tobytes(),
            sample_rate=RATE,
            num_channels=1,
            samples_per_channel=len(samples[i : i + step]),
        )
        for i in range(0, len(samples), step)
    ]


async def _collect(generator: LipSyncVideoGenerator) -> tuple[list, int, int]:
    """Read until the segment ends. Returns (items, video frames, audio samples)."""
    items, video, audio = [], 0, 0
    async for item in generator:
        items.append(item)
        if isinstance(item, AudioSegmentEnd):
            break
        if isinstance(item, rtc.VideoFrame):
            video += 1
        else:
            audio += item.samples_per_channel
    return items, video, audio


async def test_generator_pairs_each_speech_frame_with_its_audio():
    engine = _FakeEngine()
    generator = LipSyncVideoGenerator(_FakeClip(), engine, sample_rate=RATE)  # type: ignore[arg-type]
    for frame in _audio(1.01):
        await generator.push_audio(frame)
    await generator.push_audio(AudioSegmentEnd())

    items, video, audio = await asyncio.wait_for(_collect(generator), timeout=20)

    expected_frames = int(np.ceil(1.01 * FPS))
    assert video == expected_frames
    assert audio == expected_frames * (RATE // FPS)  # last frame is zero padded
    assert engine.calls == expected_frames
    # Strict alternation: every audio chunk directly follows its video frame.
    kinds = [type(i).__name__ for i in items[:-1]]
    assert kinds == ["VideoFrame", "AudioFrame"] * expected_frames
    # The mouth region was repainted, the rest of the frame was not.
    middle = items[2 * (expected_frames // 2)]
    image = np.frombuffer(middle.data, dtype=np.uint8).reshape(96, 64, 4)
    assert image[60, 32, 0] > 200 and image[5, 5, 0] == 100


async def test_generator_idles_without_inference_and_drops_cleared_speech():
    engine = _FakeEngine()
    generator = LipSyncVideoGenerator(_FakeClip(), engine, sample_rate=RATE)  # type: ignore[arg-type]
    stream = generator.__aiter__()

    first = await asyncio.wait_for(anext(stream), timeout=10)
    assert isinstance(first, rtc.VideoFrame) and engine.calls == 0

    for frame in _audio(1.0):
        await generator.push_audio(frame)
    generator.clear_buffer()  # interruption before anything was rendered
    after = await asyncio.wait_for(anext(stream), timeout=10)
    assert isinstance(after, rtc.VideoFrame) and engine.calls == 0


async def test_generator_under_load_reuses_the_mouth_on_alternate_frames(monkeypatch):
    engine = _FakeEngine()
    generator = LipSyncVideoGenerator(_FakeClip(), engine, sample_rate=RATE)  # type: ignore[arg-type]
    generator._stride = 2
    monkeypatch.setattr(generator, "_note_speed", lambda elapsed, count: None)
    for frame in _audio(1.0):
        await generator.push_audio(frame)
    await generator.push_audio(AudioSegmentEnd())

    items, video, audio = await asyncio.wait_for(_collect(generator), timeout=20)

    # Half the inference, but every frame and every audio sample still goes out.
    assert video == FPS and audio == RATE
    assert engine.calls == int(np.ceil(FPS / 2))
    frames = [i for i in items if isinstance(i, rtc.VideoFrame)]
    for frame in frames[3:-3]:  # skip the fade in and out at the utterance edges
        image = np.frombuffer(frame.data, dtype=np.uint8).reshape(96, 64, 4)
        assert image[60, 32, 0] > 200


def test_generator_raises_and_lowers_the_stride_with_render_time(monkeypatch):
    from soshop.agent.lipsync import generator as generator_module

    now = [100.0]
    monkeypatch.setattr(generator_module.time, "monotonic", lambda: now[0])
    generator = LipSyncVideoGenerator(_FakeClip(), _FakeEngine(), sample_rate=RATE)  # type: ignore[arg-type]
    budget = 1 / FPS

    now[0] += 2
    generator._note_speed(budget * 1.2, 1)  # slower than realtime
    assert generator._stride == 2

    generator._note_speed(budget * 0.2, 1)  # one lucky batch must not undo it
    assert generator._stride == 2

    now[0] += 30
    for _ in range(20):  # sustained headroom, well past the dwell time
        generator._note_speed(budget * 0.2, 1)
    assert generator._stride == 1


def test_generator_keeps_raising_the_stride_while_still_too_slow(monkeypatch):
    from soshop.agent.lipsync import generator as generator_module

    now = [100.0]
    monkeypatch.setattr(generator_module.time, "monotonic", lambda: now[0])
    generator = LipSyncVideoGenerator(_FakeClip(), _FakeEngine(), sample_rate=RATE)  # type: ignore[arg-type]
    budget = 1 / FPS

    for _ in range(10):
        now[0] += 2
        generator._note_speed(budget * 1.5, 1)

    assert generator._stride == generator_module._MAX_STRIDE


def _fake_landmarks(eyes: tuple[float, float], roll_deg: float, eye_mouth: float) -> np.ndarray:
    """106 landmarks with only the anchors the aligner reads, for a rolled head."""
    from soshop.agent.lipsync import align

    roll = np.radians(roll_deg)
    right = np.array([np.cos(roll), np.sin(roll)])
    down = np.array([-np.sin(roll), np.cos(roll)])
    center = np.array(eyes, dtype=np.float64)
    landmarks = np.zeros((106, 2), dtype=np.float32)
    landmarks[list(align._LEFT_EYE)] = center - right * 30
    landmarks[list(align._RIGHT_EYE)] = center + right * 30
    landmarks[align._MOUTH] = center + down * eye_mouth
    return landmarks


def test_alignment_puts_a_tilted_face_upright():
    from soshop.agent.lipsync import align

    landmarks = _fake_landmarks((300.0, 220.0), roll_deg=-12.0, eye_mouth=110.0)
    eyes, mouth, roll = align.anchors(landmarks)
    assert np.degrees(roll) == pytest.approx(-12.0, abs=0.01)

    to_frame = align.face_to_frame(eyes, mouth, roll, 110.0, 96)
    to_face = align.invert(to_frame)

    def project(point: np.ndarray) -> np.ndarray:
        return to_face[:, :2] @ point + to_face[:, 2]

    eyes_in_face, mouth_in_face = project(eyes), project(mouth)
    # Upright: eyes and mouth share a column, the mouth is in the lower half
    # the model repaints, and both eyes land on one row.
    assert eyes_in_face[0] == pytest.approx(48.0, abs=0.01)
    assert mouth_in_face[0] == pytest.approx(48.0, abs=0.01)
    assert mouth_in_face[1] > 48 > eyes_in_face[1]
    left = project(landmarks[list(align._LEFT_EYE)].mean(axis=0))
    right = project(landmarks[list(align._RIGHT_EYE)].mean(axis=0))
    assert left[1] == pytest.approx(right[1], abs=0.01) and left[0] < right[0]
    # And the two transforms are inverses of each other.
    back = to_frame[:, :2] @ mouth_in_face + to_frame[:, 2]
    assert back == pytest.approx(mouth, abs=0.01)


def test_clip_alignment_keeps_one_face_size_and_smooths_jitter():
    from soshop.agent.lipsync import align

    rng = np.random.default_rng(7)
    frames = [
        _fake_landmarks((300.0 + rng.normal(0, 1.5), 220.0 + rng.normal(0, 1.5)), -12.0, 110.0)
        for _ in range(40)
    ]
    transforms = align.clip_transforms(frames, 96)

    assert transforms.shape == (40, 2, 3)
    scales = np.linalg.norm(transforms[:, :, 0], axis=1)
    assert np.ptp(scales) < 1e-5  # constant size: the mouth never "breathes"
    raw_x = np.array([align.anchors(lm)[0][0] for lm in frames])
    assert np.std(transforms[:, 0, 2]) < np.std(raw_x) * 0.6  # steadier than the detections


def test_box_alignment_matches_the_plain_face_box():
    from soshop.agent.lipsync import align

    to_frame = align.box_to_frame(np.array([16, 24, 48, 72], dtype=np.int32), 96)

    assert to_frame @ np.array([0, 0, 1], dtype=np.float32) == pytest.approx([16, 24])
    assert to_frame @ np.array([96, 96, 1], dtype=np.float32) == pytest.approx([48, 72])
    assert align.bounds(to_frame, 96, 64, 96) == (16, 24, 48, 72)


def test_status_write_survives_a_reader_holding_the_file(tmp_path, monkeypatch):
    """Windows refuses the atomic replace while a poller has status.json open."""
    from pathlib import Path

    from soshop.agent.lipsync import assets

    monkeypatch.setattr(assets, "_STATUS_RETRY_S", 0.0)
    real_replace = Path.replace
    blocked = {"left": 3}

    def flaky_replace(self, target):
        if blocked["left"] > 0:
            blocked["left"] -= 1
            raise PermissionError("locked by a reader")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    assets.write_status(tmp_path, "processing", 0.4)
    assert assets.read_status(tmp_path)["progress"] == 0.4

    # A progress tick that can never land is dropped, the build goes on.
    blocked["left"] = 10_000
    assets.write_status(tmp_path, "processing", 0.5)
    assert assets.read_status(tmp_path)["progress"] == 0.4

    # A final status is not allowed to vanish silently.
    monkeypatch.setattr(assets, "_FINAL_STATUS_ATTEMPTS", 5)
    with pytest.raises(PermissionError):
        assets.write_status(tmp_path, "ready", 1.0)
    blocked["left"] = 2
    assets.write_status(tmp_path, "ready", 1.0)
    assert assets.read_status(tmp_path)["status"] == "ready"
