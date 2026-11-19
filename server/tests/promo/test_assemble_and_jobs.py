"""Stitching (ffmpeg, skipped when absent), captions and the on-disk job store."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from soshop.promo import assemble, jobs
from soshop.promo.script import PromoHost, PromoProduct

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


def test_job_store_round_trip(tmp_path: Path) -> None:
    job, path = jobs.new_job(
        tmp_path,
        product=PRODUCT,
        host=PromoHost(name="Mai"),
        language="vi",
        shot_count=3,
        flow_model="Veo 3.1 - Lite",
    )
    assert jobs.job_dir(tmp_path, job.id) == path
    job.status = "rendering"
    job.shots = [jobs.ShotState(index=0, speech_match=0.9)]
    jobs.save(path, job)
    loaded = jobs.load(path)
    assert loaded is not None and loaded.busy and loaded.shots[0].speech_match == 0.9
    assert [j.id for j in jobs.list_all(tmp_path)] == [job.id]
    assert jobs.clip_path(path, 0).name == "shot-1.mp4"


@pytest.mark.parametrize("bad", ["", "../etc", "a/b", "nope"])
def test_job_dir_rejects_unknown_or_unsafe_ids(tmp_path: Path, bad: str) -> None:
    assert jobs.job_dir(tmp_path, bad) is None
