"""Resolving broadcast videos and storing uploads (local files, ffmpeg when present)."""

from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path

import pytest

from soshop.broadcast import media
from soshop.promo import jobs
from soshop.promo.script import PromoHost, PromoProduct
from soshop_server.core.config import settings

H264_BANNER = """Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'clip.mp4':
  Duration: 00:00:26.27, start: 0.000000, bitrate: 1865 kb/s
  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p(tv, bt709, \
progressive), 720x1280 [SAR 1:1 DAR 9:16], 1700 kb/s, 30 fps, 30 tbr, 15360 tbn (default)
  Stream #0:1[0x2](und): Audio: aac (LC) (mp4a / 0x6134706D), 48000 Hz, stereo, fltp, 160 kb/s
"""


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    return tmp_path


def test_parse_probe_reads_codec_size_and_length() -> None:
    info = media.parse_probe(H264_BANNER)
    assert info is not None
    assert (info.codec, info.width, info.height) == ("h264", 720, 1280)
    assert info.duration_s == pytest.approx(26.27)
    assert info.has_audio and "mp4" in info.container


def test_parse_probe_rejects_audio_only() -> None:
    banner = "Input #0, mp3, from 'a.mp3':\n  Duration: 00:01:00.00\n  Stream #0:0: Audio: mp3\n"
    assert media.parse_probe(banner) is None


def _promo_job(*, ready: bool) -> jobs.PromoJob:
    job = jobs.new_job(
        product=PromoProduct(id="p1", name="Son dưỡng", price=59000),
        host=PromoHost(name="Mai"),
        language="vi",
        shot_count=3,
        flow_model="Veo 3.1 - Lite",
    )
    job.thumb_key = jobs.new_key(job.id, "thumb", "jpg")
    if ready:
        job.video_key = jobs.new_key(job.id, "video", "mp4")
        job.status, job.duration_s = "ready", 26.27
    else:
        job.status = "rendering"
    return job


def test_resolve_promo_ready_video(data_dir: Path) -> None:
    job = _promo_job(ready=True)
    found = media.resolve_promo(job)
    assert found.playable and found.problem is None
    assert found.video_url == f"/media/{job.video_key}"
    assert found.thumb_url == f"/media/{job.thumb_key}"
    assert found.video_s == pytest.approx(26.27)
    assert found.product is not None and found.product.name == "Son dưỡng"
    assert found.host_name == "Mai" and found.captions_url is None
    job.captions_key = jobs.new_key(job.id, "captions", "vtt")
    captions = media.resolve_promo(job).captions_url
    assert captions is not None and captions.startswith(f"/promo/videos/{job.id}/captions?v=")


def test_resolve_promo_busy_or_missing(data_dir: Path) -> None:
    busy = media.resolve_promo(_promo_job(ready=False))
    assert not busy.playable and busy.problem == "Video đang được tạo lại."
    assert busy.thumb_url is not None
    gone = media.resolve_promo(None)
    assert not gone.playable and gone.problem == "Video đã bị xóa."


def test_resolve_upload(data_dir: Path) -> None:
    uploads = data_dir / "uploads"
    uploads.mkdir()
    (uploads / "abc.mp4").write_bytes(b"mp4")
    (uploads / "abc.jpg").write_bytes(b"jpg")
    found = media.resolve_upload("/media/uploads/abc.mp4", 12.5)
    assert found.playable and found.thumb_url == "/media/uploads/abc.jpg"
    assert not media.resolve_upload("/media/uploads/nope.mp4", 12.5).playable
    assert not media.resolve_upload("/media/uploads/abc.mp4", None).playable
    assert not media.resolve_upload("../../etc/passwd", 3).playable


needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def _test_video(path: Path, codec: str = "libx264") -> None:
    sources = ["-f", "lavfi", "-i", "testsrc=size=180x320:rate=15:duration=2"]
    sources += ["-f", "lavfi", "-i", "sine=frequency=440:duration=2"]
    encode = ["-c:v", codec, "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)]
    subprocess.run(  # noqa: S603 - fixed argv
        [shutil.which("ffmpeg") or "ffmpeg", "-loglevel", "error", "-y", *sources, *encode],
        check=True,
    )


@needs_ffmpeg
def test_save_upload_stores_video_thumb_and_sidecar(data_dir: Path, tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    _test_video(source)
    saved = media.save_upload(io.BytesIO(source.read_bytes()), "Mở đầu.mp4")
    assert saved.url.startswith("/media/uploads/") and saved.url.endswith(".mp4")
    assert (saved.width, saved.height) == (180, 320)
    assert saved.duration_s == pytest.approx(2.0, abs=0.2)
    assert saved.thumb_url is not None and saved.name == "Mở đầu.mp4"
    info = media.upload_info(saved.url)
    assert info is not None and info["codec"] == "h264"
    assert not list((data_dir / "work" / "uploads").iterdir())


@needs_ffmpeg
def test_save_upload_rejects_non_video_and_unplayable_codecs(
    data_dir: Path, tmp_path: Path
) -> None:
    with pytest.raises(media.UploadRejectedError):
        media.save_upload(io.BytesIO(b"definitely not a video"), "x.mp4")
    mpeg4 = tmp_path / "old.mp4"
    _test_video(mpeg4, codec="mpeg4")
    with pytest.raises(media.UploadRejectedError, match="MPEG4"):
        media.save_upload(io.BytesIO(mpeg4.read_bytes()), "old.mp4")
    assert not list((data_dir / "work" / "uploads").iterdir())
