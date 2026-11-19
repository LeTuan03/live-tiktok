"""Hermetic tests for the pre-rendered ad video (no models, no network, no ffmpeg).

The render itself needs LivePortrait weights and is exercised by the CLI. These
tests cover the logic around it: script parsing and fallback, the voice
layout, mouth opening from audio, the director's plan, and the edit's shots,
captions and overlays.
"""

from __future__ import annotations

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from soshop.advideo import director, edit, mouth, voice  # noqa: E402
from soshop.advideo.script import (  # noqa: E402
    AdScript,
    Beat,
    ProductBrief,
    ScriptError,
    fallback_script,
    parse_script,
)

RATE = voice.RATE
FPS = 25
PRODUCT = ProductBrief(
    name="Son kem lì màu đỏ gạch",
    price=199000,
    description="Son kem lì chất mềm mịn. Lên màu chuẩn.",
    highlights="Không làm khô môi; Giữ màu tốt; Hợp nhiều tông da",
)


# ------------------------------------------------------------------ script


def test_parse_script_skips_reasoning_and_clamps_fields():
    reply = """<think>let me plan</think>Here you go: {"hook_title": "Son đỏ gạch 199k",
      "beats": [
        {"text": "Bạn đang tìm son đỏ gạch?", "gesture": "emphasis", "show_product": false},
        {"text": "Chất son **mềm mịn** 😍", "gesture": "dance", "show_product": true},
        {"text": "   ", "gesture": "nod"},
        {"text": "Chỉ một trăm chín mươi chín nghìn thôi.", "gesture": "smile"}
      ], "cta": "Mua ngay hôm nay đi cả nhà ơi"}"""
    script = parse_script(reply)
    assert [b.gesture for b in script.beats] == ["emphasis", "engage", "smile"]
    assert script.beats[1].show_product and "*" not in script.beats[1].text
    assert "😍" not in script.beats[1].text
    assert len(script.cta) <= 16 and script.hook_title == "Son đỏ gạch 199k"


def test_parse_script_rejects_replies_without_enough_beats():
    with pytest.raises(ScriptError):
        parse_script("no json here")
    with pytest.raises(ScriptError):
        parse_script('{"beats": [{"text": "Một câu thôi."}]}')


def test_script_round_trips_through_json():
    script = parse_script(
        '{"hook_title": "T", "cta": "Mua", "beats": [{"text": "Một hai ba."}, '
        '{"text": "Bốn năm sáu.", "gesture": "nod"}, {"text": "Bảy tám chín."}]}'
    )
    assert AdScript.from_json(script.to_json()) == script


def test_fallback_script_uses_only_the_given_facts():
    script = fallback_script(PRODUCT)
    text = " ".join(b.text for b in script.beats)
    assert len(script.beats) >= 3 and any(b.show_product for b in script.beats)
    assert "199.000 đồng" in text and PRODUCT.name in text
    assert "không làm khô môi" in text


# ------------------------------------------------------------------- voice


def _tone(seconds: float, hz: float, amp: float = 0.4) -> np.ndarray:
    t = np.arange(int(RATE * seconds)) / RATE
    return (np.sin(2 * np.pi * hz * t) * amp * 32767).astype(np.int16)


def test_trim_silence_keeps_only_the_voiced_part():
    clip = np.concatenate(
        [np.zeros(RATE // 2, np.int16), _tone(1.0, 300), np.zeros(RATE, np.int16)]
    )
    trimmed = voice.trim_silence(clip)
    assert 0.95 * RATE < len(trimmed) < 1.2 * RATE
    assert voice.trim_silence(np.zeros(RATE, np.int16)).size == 0


def test_assemble_lays_beats_out_with_pauses():
    texts = ["Bạn biết chưa?", "Son này mềm lắm.", "Mua ngay nha."]
    track = voice.assemble(texts, [_tone(1.0, 300), _tone(1.5, 300), _tone(0.8, 300)])
    starts = [b.start for b in track.beats]
    assert starts[0] == pytest.approx(voice.LEAD_IN_S, abs=1e-3)
    # A question gets a longer pause after it than a statement.
    assert starts[1] - track.beats[0].end == pytest.approx(voice.GAP_AFTER_QUESTION_S, abs=1e-3)
    assert starts[2] - track.beats[1].end == pytest.approx(voice.GAP_S, abs=1e-3)
    assert track.duration == pytest.approx(track.beats[-1].end + voice.TAIL_S, abs=1e-3)
    words = track.beats[1].words
    assert [w.text for w in words] == texts[1].split()
    assert words[0].start == track.beats[1].start and words[-1].end == pytest.approx(
        track.beats[1].end
    )
    assert all(a.end <= b.start + 1e-9 for a, b in zip(words, words[1:], strict=False))


# ------------------------------------------------------------------- mouth


def test_open_vowels_open_the_mouth_more_than_hums_and_silence_closes_it():
    vowel = _tone(0.6, 800)  # energy in the first-formant band, like "a"
    hum = _tone(0.6, 180)  # a nasal murmur, mostly below it
    silence = np.zeros(int(RATE * 0.6), np.int16)
    audio = np.concatenate([silence, vowel, hum, silence])
    curve = mouth.openness(audio, RATE, FPS)
    assert len(curve) == int(len(audio) * FPS // RATE)
    seg = int(0.6 * FPS)
    assert curve[seg + 4 : 2 * seg - 2].mean() > 0.6
    assert curve[2 * seg + 4 : 3 * seg - 2].mean() < 0.3
    assert curve[: seg - 2].max() < 0.05 and curve[-seg + 4 :].max() < 0.2
    assert mouth.openness(np.zeros(RATE, np.int16), RATE, FPS).max() == 0.0


# ---------------------------------------------------------------- director


def _syllables(seconds: float) -> np.ndarray:
    """Speech-like audio: 5 syllables a second, every third one stressed."""
    out = []
    for k in range(int(seconds * 5)):
        amp = 0.6 if k % 3 == 0 else 0.25
        out += [_tone(0.14, 750, amp), np.zeros(int(RATE * 0.06), np.int16)]
    return np.concatenate(out)


def _track_and_script() -> tuple[voice.VoiceTrack, AdScript]:
    texts = [
        "Bạn đang tìm son đỏ gạch dễ dùng không?",
        "Chất son mềm mịn và dễ tán.",
        "Chỉ một trăm chín mươi chín nghìn thôi.",
    ]
    script = AdScript(
        "Son 199k",
        tuple(Beat(t, g) for t, g in zip(texts, ("emphasis", "smile", "nod"), strict=True)),
        "Mua ngay",
    )
    track = voice.assemble(texts, [_syllables(2.4), _syllables(3.6), _syllables(2.0)])
    return track, script


def test_director_moves_the_mouth_only_while_talking():
    track, script = _track_and_script()
    frames = director.direct(track, script, FPS)
    assert len(frames) == voice.frame_count(track, FPS)
    lips = np.array([f.lip for f in frames])
    first = track.beats[0]
    assert lips[: int((first.start - 0.15) * FPS)].max() == 0.0  # lead-in: mouth closed
    assert lips[-int(0.7 * FPS) :].max() == 0.0  # tail: closed again
    spoken = lips[int(first.start * FPS) : int(first.end * FPS)]
    assert spoken.max() > 0.2 and (spoken < 0.08).any()  # opens and closes per syllable
    assert lips.max() <= 0.3 + 1e-9


def test_director_motion_is_smooth_and_alive():
    track, script = _track_and_script()
    frames = director.direct(track, script, FPS)
    pitch = np.array([f.motion.pitch for f in frames])
    yaw = np.array([f.motion.yaw for f in frames])
    assert np.abs(np.diff(pitch)).max() < 2.0 and np.abs(np.diff(yaw)).max() < 2.0
    assert np.ptp(pitch) > 5.0  # gestures and beat nods, not a frozen head
    lids = np.array([f.motion.exp[11, 1] for f in frames])
    blinks = int(np.sum((lids[1:] > 0.004) & (lids[:-1] <= 0.004)))
    assert blinks >= 3
    assert len(director.nod_events(track, FPS)) >= 2


def test_slots_cover_the_video_and_start_beats_with_the_scripted_gesture():
    from soshop.agent.lipsync.motion import TALK_GESTURES

    track, script = _track_and_script()
    slots = director.plan_slots(track, script, np.random.default_rng(1))
    assert slots[0].start == 0.0 and slots[-1].end == pytest.approx(track.duration)
    assert all(a.end == pytest.approx(b.start) for a, b in zip(slots, slots[1:], strict=False))
    for spoken in track.beats:
        first = next(s for s in slots if s.start == pytest.approx(spoken.start))
        assert first.gesture is TALK_GESTURES[script.beats[spoken.index].gesture]


# -------------------------------------------------------------------- edit


def test_shots_cut_on_beats_and_keep_overlay_beats_wide():
    track, script = _track_and_script()
    shots = edit.plan_shots(track, script, has_photo=False)
    assert [s.start for s in shots] == [0.0, track.beats[1].start, track.beats[2].start]
    assert shots[0].zoom == 1.0 and shots[-1].zoom == 1.0  # hook title, call-to-action
    assert shots[1].zoom > 1.0


def test_caption_chunks_stay_short_and_inside_their_beat():
    track, _ = _track_and_script()
    chunks = edit.caption_chunks(track)
    assert all(1 <= len(c.words) <= 3 for c in chunks)
    words = [w.text for c in chunks for w in c.words]
    assert words == [w.text for b in track.beats for w in b.words]
    for chunk in chunks:
        beat = next(b for b in track.beats if b.start <= chunk.start < b.end)
        assert chunk.end <= beat.end + 1e-9


def test_screen_price_and_blend_clipping():
    assert edit.screen_price(PRODUCT) == "199.000đ"
    assert edit.screen_price(ProductBrief("x")) is None
    canvas = np.zeros((40, 40, 3), np.uint8)
    layer = np.zeros((20, 20, 4), np.uint8)
    layer[:, :, 2] = 255
    layer[:, :, 3] = 255
    edit.blend(canvas, layer, 2, 2)  # mostly off the top-left corner
    assert canvas[0, 0, 2] == 255 and canvas[15, 15, 2] == 0


def test_cut_draws_a_full_frame_with_captions():
    try:
        edit.font_path()
    except FileNotFoundError:
        pytest.skip("no bold system font on this machine")
    from soshop.agent.lipsync.performance import FaceAnchor

    track, script = _track_and_script()
    cut = edit.Cut(track, script, PRODUCT)
    frame = np.full((1280, 720, 3), 90, np.uint8)
    face = FaceAnchor(360, 300, 300, 400)
    during = cut.draw(frame.copy(), track.beats[0].start + 0.3, face)
    assert during.shape == (edit.OUT_H, edit.OUT_W, 3)
    caption_row = during[int(edit.OUT_H * 0.72)]
    assert (caption_row > 200).any()  # white caption text drawn over the grey frame
    silent = cut.draw(frame.copy(), 0.1, face)
    assert not (silent[int(edit.OUT_H * 0.72)] > 200).any()  # no caption before speech
