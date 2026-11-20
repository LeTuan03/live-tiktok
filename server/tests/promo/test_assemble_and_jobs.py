"""Stitching (ffmpeg, skipped when absent), captions and the job model (no database)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from soshop.promo import assemble, jobs, runner
from soshop.promo.script import PromoHost, PromoProduct, PromoScript, Shot
from soshop_server import storage
from soshop_server.core.config import settings

PRODUCT = PromoProduct(id="p1", name="Son dưỡng môi Labello Classic", price=59000)

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def test_webvtt_uses_speech_spans_and_shot_offsets() -> None:
    vtt = assemble.webvtt(["một", "hai"], [8.0, 7.5], [(0.4, 4.5), None])
    assert vtt.startswith("WEBVTT")
    assert "00:00:00.400 --> 00:00:04.500\nmột" in vtt
    # No span: the whole second shot minus lead-in and tail, offset by shot one.
    assert "00:00:08.300 --> 00:00:15.200\nhai" in vtt


def test_webvtt_ignores_degenerate_spans() -> None:
    vtt = assemble.webvtt(["một"], [8.0], [(3.0, 3.1)])
    assert "00:00:00.300 --> 00:00:07.700" in vtt


def test_screen_price_formats() -> None:
    assert assemble.screen_price(PRODUCT) == "59.000đ"
    usd = PRODUCT.model_copy(update={"price": 15.5, "currency": "USD"})
    assert assemble.screen_price(usd) == "$15.5"
    assert assemble.screen_price(PRODUCT.model_copy(update={"price": None})) is None


def test_end_card_is_vertical_even_without_photo() -> None:
    image = assemble.end_card_image(PRODUCT, None, "Mua ngay")
    assert image.size == (assemble.WIDTH, assemble.HEIGHT)


def _test_clip(path: Path, seconds: int, size: str, audio: bool) -> None:
    args = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size={size}:rate=24",
    ]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100"]
    args += ["-t", str(seconds), "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(args, check=True)  # noqa: S603 - fixed argv in a test


@needs_ffmpeg
def test_normalise_concat_and_thumbnail(tmp_path: Path) -> None:
    raw_a, raw_b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    _test_clip(raw_a, 2, "720x1280", audio=True)
    _test_clip(raw_b, 2, "1280x720", audio=False)  # landscape, silent: must still join
    parts = [tmp_path / "na.mp4", tmp_path / "nb.mp4"]
    durations = [
        assemble.normalise(raw, dst) for raw, dst in zip((raw_a, raw_b), parts, strict=True)
    ]
    assert all(1.9 < d < 2.2 for d in durations)
    end = tmp_path / "end.mp4"
    assemble.end_card_clip(PRODUCT, None, "Mua ngay", end)
    video = tmp_path / "video.mp4"
    assemble.concat([*parts, end], video)
    total, has_audio = assemble.probe(video)
    assert has_audio and 6.0 < total < 7.0
    assemble.thumbnail(video, tmp_path / "thumb.jpg", at_s=0.5)
    assert (tmp_path / "thumb.jpg").stat().st_size > 0


def _job() -> jobs.PromoJob:
    return jobs.new_job(
        product=PRODUCT,
        host=PromoHost(name="Mai"),
        language="vi",
        shot_count=3,
        flow_model="Veo 3.1 - Lite",
    )


def test_new_job_is_a_draft_with_a_safe_id() -> None:
    job = _job()
    assert job.status == "draft" and not job.busy
    assert jobs.valid_id(job.id) and len(job.id) == 32
    assert job.file_keys() == []


def test_new_keys_live_under_the_job_and_never_repeat() -> None:
    job = _job()
    first = jobs.new_key(job.id, "video", "mp4")
    second = jobs.new_key(job.id, "video", "mp4")
    assert first != second
    assert first.startswith(f"promo/{job.id}/video-") and first.endswith(".mp4")
    assert jobs.key(job.id, "thumb.jpg") == f"promo/{job.id}/thumb.jpg"


def test_drop_clip_forgets_the_take_and_returns_its_key() -> None:
    job = _job()
    shot = jobs.ShotState(index=0, has_clip=True, clip_key=jobs.new_key(job.id, "shot-1", "mp4"))
    job.shots = [shot]
    job.video_key = jobs.new_key(job.id, "video", "mp4")
    assert set(job.file_keys()) == {shot.clip_key, job.video_key}
    key = shot.drop_clip()
    assert key is not None and key.startswith(f"promo/{job.id}/shot-1-")
    assert shot.clip_key is None and not shot.has_clip


def test_job_state_round_trips_through_json() -> None:
    job = _job()
    job.status = "rendering"
    job.shots = [jobs.ShotState(index=0, speech_match=0.9, clip_key="promo/x/shot-1-a.mp4")]
    loaded = jobs.PromoJob.model_validate_json(job.model_dump_json())
    assert loaded.busy and loaded.shots[0].speech_match == 0.9
    assert loaded.shots[0].clip_key == "promo/x/shot-1-a.mp4"


@pytest.mark.parametrize("bad", ["", "../etc", "a/b", "ố" * 3, "x" * 65])
def test_unsafe_ids_are_rejected(bad: str) -> None:
    assert not jobs.valid_id(bad)
    with pytest.raises(ValueError):
        jobs.work_dir(bad)


@needs_ffmpeg
def test_runner_assembles_from_stored_clips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clips come from the store (not scratch); the results go back under new keys."""
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(settings, "MEDIA_STORAGE", "local")
    store = storage.get_store()
    job = _job()
    line = "Son này mềm môi lắm nha."
    shot = Shot(
        beat="hook",
        action="Cầm thỏi son lên trước ống kính",
        camera="cận",
        emotion="vui",
        dialogue=line,
    )
    job.script = PromoScript(angle="a", product_look="b", setting="c", shots=[shot, shot])
    for index in range(2):
        raw = tmp_path / f"raw-{index}.mp4"
        _test_clip(raw, 1, "720x1280", audio=True)
        key = jobs.new_key(job.id, f"shot-{index + 1}", "mp4")
        store.put_file(key, raw, "video/mp4")
        job.shots.append(jobs.ShotState(index=index, status="done", has_clip=True, clip_key=key))

    work = jobs.work_dir(job.id)
    made = runner._assemble(work, job, None)

    assert {"video_key", "thumb_key", "captions_key", "duration_s"} <= set(made)
    for name in ("video_key", "thumb_key", "captions_key"):
        key = made[name]
        assert isinstance(key, str) and key.startswith(f"promo/{job.id}/") and store.exists(key)
    captions = store.get_bytes(str(made["captions_key"]))
    assert captions is not None and line in captions.decode("utf-8")
    assert isinstance(made["duration_s"], float) and made["duration_s"] > 2.0
