"""Hermetic tests for the avatar's behavior layers (no model weights, no network).

Covers the two-track motion script (idle and talk share portal poses), the
realtime performance warp (breathing, sway, beat nods, camera), track
switching in the generator, the v1/v2 asset formats and the teeth color fix.
"""

from __future__ import annotations

import asyncio
import json

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from livekit import rtc  # noqa: E402
from livekit.agents.voice.avatar import AudioSegmentEnd  # noqa: E402

from soshop.agent.lipsync import assets  # noqa: E402
from soshop.agent.lipsync.generator import (  # noqa: E402
    LipSyncVideoGenerator,
    _inner_mouth_mask,
    _whiten_teeth,
)
from soshop.agent.lipsync.motion import IDLE, TALK, MotionScript  # noqa: E402
from soshop.agent.lipsync.performance import FaceAnchor, Performer, loudness  # noqa: E402

RATE = 24000
FPS = 25


# ------------------------------------------------------------------ motion


def test_both_tracks_loop_seamlessly():
    script = MotionScript(300, FPS)
    for track in (IDLE, TALK):
        first, wrapped = script.channels(track, 0), script.channels(track, 300)
        for name, value in first.items():
            assert value == pytest.approx(wrapped[name], abs=1e-9), name
        # Neighbouring frames never jump: the loop has no seam anywhere.
        poses = np.array(
            [[script.channels(track, i)[c] for c in ("pitch", "yaw", "roll")] for i in range(301)]
        )
        assert np.abs(np.diff(poses, axis=0)).max() < 2.0


def test_tracks_meet_at_every_slot_boundary_and_differ_in_between():
    script = MotionScript(300, FPS)
    for seconds in script.portal_seconds:
        index = round(seconds * FPS)
        if index >= 300:
            continue
        idle, talk = script.channels(IDLE, index), script.channels(TALK, index)
        gap = sum(abs(idle[c] - talk[c]) for c in ("pitch", "yaw", "roll"))
        assert gap < 0.5, seconds

    portals = script.portals()
    assert portals[0] and 0.1 < portals.mean() < 0.5
    # Between portals the talk track moves the head clearly more.
    spread = {
        track: np.ptp([script.channels(track, i)["pitch"] for i in range(300)])
        for track in (IDLE, TALK)
    }
    assert spread[TALK] > 6.0
    differs = [
        abs(script.channels(IDLE, i)["pitch"] - script.channels(TALK, i)["pitch"])
        for i in range(300)
    ]
    assert max(differs) > 3.0


def test_talk_track_uses_every_gesture_and_blinks_more():
    from soshop.agent.lipsync.motion import _TALK_GESTURES

    script = MotionScript(300, FPS)
    used = {slot.gestures[TALK] for slot in script._slots}
    assert used == set(_TALK_GESTURES)

    def blinks(track: int) -> int:
        lids = [script.channels(track, i)["blink"] > 8 for i in range(300)]
        return sum(1 for a, b in zip(lids, lids[1:], strict=False) if b and not a)

    assert blinks(TALK) > blinks(IDLE) >= 5


# ------------------------------------------------------------- performance


def _performer(matte: np.ndarray | None = None) -> Performer:
    return Performer(540, 960, FPS, FaceAnchor(cx=297, cy=260, width=246, height=340), matte)


def test_beat_nods_follow_stressed_syllables_not_steady_speech():
    performer = _performer()
    nods = []
    for _ in range(50):  # level speech: one nod as it starts, then nothing stands out
        performer.step(True, -24.0)
        nods.append(performer.nod)
    assert max(nods[:12]) > 2.0 and max(nods[15:]) < 0.01

    trace = []
    for i in range(20):
        performer.step(True, -10.0 if i in (2, 3) else -24.0)
        trace.append(performer.nod)
    peak = int(np.argmax(trace))
    assert 3.0 < max(trace) <= 0.0042 * 960 + 1e-6
    assert 3 <= peak <= 8  # about 160 ms after the kick, not instantly
    assert trace[-1] < 0.05  # and back up within half a second


def test_activity_eases_in_while_talking_and_out_after():
    performer = _performer()
    for _ in range(5):
        performer.step(True)
    early = performer.activity
    for _ in range(60):
        performer.step(True)
    assert 0 < early < 0.3 and performer.activity > 0.9
    for _ in range(60):
        performer.step(False)
    assert performer.activity < 0.1


def test_body_moves_more_than_the_background():
    matte = np.zeros((960, 540), dtype=np.float32)
    matte[300:, :] = 1.0  # the body fills the bottom, the top is background
    performer = _performer(matte)
    background, body = [], []
    for i in range(250):
        performer.step(i > 60, -20.0 if i % 15 == 0 else -28.0)
        vx, vy = performer.displacement()
        background.append(vy[2, 2])  # top-left corner, grid coordinates
        body.append(vy[75, 34])  # about y 600, x 270
    # Both share the camera drift; the body also breathes, sways and leans.
    assert np.std(np.diff(body)) > np.std(np.diff(background)) * 1.5


def test_warp_stays_inside_the_frame_and_keeps_flat_images_flat():
    performer = _performer()
    flat = np.full((960, 540, 3), 90, dtype=np.uint8)
    for i in range(200):
        performer.step(i > 20, -15.0 if i % 9 == 0 else -30.0)
        vx, vy = performer.displacement()
        assert np.abs(vx).max() < 25 and np.abs(vy).max() < 30
    out = performer.warp(flat)
    assert out.shape == flat.shape and int(np.abs(out.astype(int) - 90).max()) == 0


def test_loudness_of_silence_and_tone():
    assert loudness(b"") is None
    tone = (np.sin(np.arange(2400) * 0.1) * 16384).astype(np.int16).tobytes()
    assert loudness(tone) == pytest.approx(-9.0, abs=0.2)  # half scale sine
    assert loudness(b"\x00\x00" * 100) < -100


# --------------------------------------------------------------- generator


class _TwoTrackClip:
    """Ten frames, two tracks. Pixel value encodes the track; portals at 0 and 5."""

    width, height, fps = 64, 96, FPS
    n_tracks = 2
    portals = np.array([True, False, False, False, False, True, False, False, False, False])
    align = None

    def __init__(self) -> None:
        self.faces = np.tile(np.array([16, 24, 48, 72], dtype=np.int32), (10, 1))

    def __len__(self) -> int:
        return 10

    def frame(self, index: int, track: int = 0) -> np.ndarray:
        return np.full((self.height, self.width, 3), 100 + 50 * track, dtype=np.uint8)


class _FakeEngine:
    def infer(self, mel: np.ndarray, face: np.ndarray) -> np.ndarray:
        return face.copy()


def _speech(seconds: float) -> list[rtc.AudioFrame]:
    samples = (np.sin(np.arange(int(RATE * seconds)) * 0.05) * 8000).astype(np.int16)
    step = RATE // 50
    return [
        rtc.AudioFrame(samples[i : i + step].tobytes(), RATE, 1, len(samples[i : i + step]))
        for i in range(0, len(samples), step)
    ]


async def test_generator_switches_track_only_at_portals():
    clip = _TwoTrackClip()
    generator = LipSyncVideoGenerator(
        clip,  # type: ignore[arg-type]
        _FakeEngine(),  # type: ignore[arg-type]
        sample_rate=RATE,
        perform=False,
    )
    stream = generator.__aiter__()
    log: list[tuple[int, int]] = []  # (timeline index, track) per video frame

    async def read_video(count: int) -> None:
        while count:
            item = await asyncio.wait_for(anext(stream), timeout=10)
            if isinstance(item, rtc.VideoFrame):
                value = np.frombuffer(item.data, dtype=np.uint8)[0]
                log.append(((len(log)) % 10, 0 if value == 100 else 1))
                count -= 1

    await read_video(23)  # past the initial "recently talked" window: idle now
    assert log[-1][1] == IDLE
    for frame in _speech(1.0):
        await generator.push_audio(frame)
    await generator.push_audio(AudioSegmentEnd())
    await read_video(60)

    switches = [i for i in range(1, len(log)) if log[i][1] != log[i - 1][1]]
    assert len(switches) >= 2  # into talk, and back to idle after the silence
    for i in switches:
        assert clip.portals[log[i][0]], f"switched at timeline index {log[i][0]}"
    assert log[switches[0]][1] == TALK and log[-1][1] == IDLE


# ------------------------------------------------------------------ assets


def _write_clip(path, *, version: int, tracks: np.ndarray | None) -> None:
    frames = [np.full((8, 8, 3), v, dtype=np.uint8) for v in (40, 120, 200)]
    offsets, blob = [0], b""
    for frame in frames:
        ok, jpeg = cv2.imencode(".jpg", frame)
        assert ok
        blob += jpeg.tobytes()
        offsets.append(len(blob))
    (path / assets.FRAMES_FILE).write_bytes(blob)
    np.save(path / assets.INDEX_FILE, np.asarray(offsets, dtype=np.int64))
    timeline = 3 if tracks is None else tracks.shape[1]
    np.save(path / assets.FACES_FILE, np.zeros((timeline, 4), dtype=np.int32))
    np.save(path / assets.ALIGN_FILE, np.zeros((timeline, 2, 3), dtype=np.float32))
    if tracks is not None:
        np.save(path / assets.TRACKS_FILE, tracks)
        np.save(path / assets.PORTALS_FILE, np.array([True, False]))
    meta = {"version": version, "width": 8, "height": 8, "fps": FPS, "frames": timeline}
    (path / assets.META_FILE).write_text(json.dumps(meta))
    assets.write_status(path, "ready", 1.0)


def test_version_one_clips_load_as_a_single_track(tmp_path):
    _write_clip(tmp_path, version=1, tracks=None)
    assert assets.assets_ready(tmp_path)
    clip = assets.MotionClip.load(tmp_path)
    assert clip.n_tracks == 1 and len(clip) == 3
    assert clip.portals.all()
    assert clip.align is not None and clip.align.shape == (1, 3, 2, 3)
    # Asking for the talk track of a one-track clip falls back to its only track.
    assert abs(int(clip.frame(1, TALK).mean()) - 120) < 4


def test_version_two_clips_map_tracks_onto_shared_frames(tmp_path):
    tracks = np.array([[0, 1], [0, 2]], dtype=np.int32)  # frame 0 shared by both
    _write_clip(tmp_path, version=2, tracks=tracks)
    clip = assets.MotionClip.load(tmp_path)
    assert assets.assets_ready(tmp_path)
    assert clip.n_tracks == 2 and len(clip) == 2
    assert abs(int(clip.frame(1, IDLE).mean()) - 120) < 4
    assert abs(int(clip.frame(1, TALK).mean()) - 200) < 4
    assert abs(int(clip.frame(0, TALK).mean()) - 40) < 4
    assert list(clip.portals) == [True, False]


# ------------------------------------------------------------------- teeth


def test_teeth_whitening_touches_only_new_bright_pixels_in_the_mouth():
    reference = np.full((96, 96, 3), (120, 140, 200), dtype=np.uint8)  # warm skin
    reference[60:66, 36:60] = (70, 60, 170)  # closed red lips
    synced = reference.astype(np.float32).copy()
    synced[61:65, 40:56] = (120, 190, 230)  # orange "teeth" where the lips were

    out = _whiten_teeth(synced, reference, _inner_mouth_mask())

    def chroma(bgr: np.ndarray) -> float:
        lab = cv2.cvtColor(np.clip(bgr, 0, 255).astype(np.uint8)[None, None], cv2.COLOR_BGR2LAB)
        return float(np.hypot(lab[0, 0, 1] - 128.0, lab[0, 0, 2] - 128.0))

    assert chroma(out[63, 48]) < chroma(synced[63, 48]) * 0.6  # teeth lose their orange
    np.testing.assert_allclose(out[20, 20], synced[20, 20], atol=2)  # skin is untouched
    np.testing.assert_allclose(out[61, 37], synced[61, 37], atol=2)  # so are the lips
