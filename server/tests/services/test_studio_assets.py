"""Local upload storage and motion-avatar status for the live studio."""

from __future__ import annotations

import io
import json
import os
import time
from pathlib import Path

import pytest
from PIL import Image

from soshop.services import studio_assets


@pytest.fixture(autouse=True)
def _data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(studio_assets.settings, "DATA_DIR", str(tmp_path))
    return tmp_path


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_upload_round_trips_through_its_media_url() -> None:
    url = studio_assets.save_image_upload(_png())

    assert url.startswith("/media/uploads/") and url.endswith(".png")
    path = studio_assets.resolve_media_path(url)
    assert path is not None and path.read_bytes() == _png()
    assert studio_assets.image_source_for_agent(url) == str(path)


@pytest.mark.parametrize("data", [b"", b"not an image at all", b"GIF89a" + b"\x00" * 20])
def test_upload_rejects_non_images(data: bytes) -> None:
    with pytest.raises(studio_assets.UploadRejectedError):
        studio_assets.save_image_upload(data)


def test_upload_rejects_oversized_files() -> None:
    with pytest.raises(studio_assets.UploadRejectedError):
        studio_assets.save_image_upload(b"\x89PNG" + b"0" * (studio_assets.MAX_UPLOAD_BYTES + 1))


@pytest.mark.parametrize(
    "url",
    [
        "/media/uploads/../../.env",
        r"/media/uploads/..\secrets.txt",
        "/media/uploads/.hidden",
        "/media/uploads/",
        "/etc/passwd",
        None,
    ],
)
def test_media_path_never_escapes_the_uploads_dir(url: str | None, _data_dir: Path) -> None:
    (_data_dir / ".env").write_text("SECRET=1", encoding="utf-8")
    assert studio_assets.resolve_media_path(url) is None


def test_agent_image_source_passes_http_urls_through() -> None:
    assert studio_assets.image_source_for_agent("https://cdn.example/a.png") == (
        "https://cdn.example/a.png"
    )
    assert studio_assets.image_source_for_agent("/media/uploads/missing.png") is None


def test_avatar_status_defaults_to_none() -> None:
    assert studio_assets.read_avatar_status("m1").status == "none"


def test_avatar_status_reads_builder_progress() -> None:
    out = studio_assets.avatar_dir("m1")
    out.mkdir(parents=True)
    (out / "status.json").write_text(
        json.dumps({"status": "processing", "progress": 0.4, "error": None}), encoding="utf-8"
    )

    status = studio_assets.read_avatar_status("m1")
    assert (status.status, status.progress) == ("processing", 0.4)


def test_stalled_build_is_reported_failed_so_it_can_be_restarted() -> None:
    out = studio_assets.avatar_dir("m1")
    out.mkdir(parents=True)
    status_path = out / "status.json"
    status_path.write_text(json.dumps({"status": "processing", "progress": 0.4}), encoding="utf-8")
    old = time.time() - 3600
    os.utime(status_path, (old, old))

    assert studio_assets.read_avatar_status("m1").status == "failed"


def test_removing_a_model_deletes_its_portrait_and_avatar_assets() -> None:
    url = studio_assets.save_image_upload(_png())
    portrait = studio_assets.resolve_media_path(url)
    assets = studio_assets.avatar_dir("m1")
    assets.mkdir(parents=True)
    (assets / "status.json").write_text("{}", encoding="utf-8")

    studio_assets.remove_model_files("m1", url)

    assert portrait is not None and not portrait.exists()
    assert not assets.exists()
    # A remote portrait is left alone and a second call is harmless.
    studio_assets.remove_model_files("m1", "https://cdn.example/a.png")
