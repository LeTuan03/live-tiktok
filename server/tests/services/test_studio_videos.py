"""Review video jobs for the studio: request building, one-at-a-time, status, cleanup.

File-based and hermetic: the render process is replaced by a fake Popen.
"""

from __future__ import annotations

import io
import json
import os
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from soshop.services import studio_assets, studio_videos
from soshop.services.studio import ModelRow, ProductRow


@pytest.fixture(autouse=True)
def _data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(studio_assets.settings, "DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []

    class _FakePopen:
        def __init__(self, args: list[str], **_: Any) -> None:
            calls.append(args)

    monkeypatch.setattr(studio_videos.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(studio_videos, "renderer_available", lambda: True)
    return calls


def _portrait_url() -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(buffer, format="PNG")
    return studio_assets.save_image_upload(buffer.getvalue())


def _model(image_url: str | None) -> ModelRow:
    return ModelRow(
        id="m1",
        name="Linh",
        intro="",
        personality="",
        language="vi",
        voice="some-elevenlabs-id",
        image_url=image_url,
        created_at=datetime.now(UTC),
    )


def _product() -> ProductRow:
    return ProductRow(
        id="p1",
        session_id="s1",
        position=0,
        name="Son kem lì",
        price=Decimal("199000"),
        currency="VND",
        description="Mềm mịn.",
        highlights="Không khô môi",
        image_url=None,
        script="",
        status="pending",
    )


def test_start_writes_the_request_and_spawns_one_render(spawned: list[list[str]]) -> None:
    job = studio_videos.start_video(_model(_portrait_url()), _product())

    assert job.status == "processing" and job.title == "Son kem lì" and not job.has_video
    assert len(spawned) == 1 and spawned[0][1:3] == ["-m", "soshop.advideo"]
    request_path = Path(spawned[0][spawned[0].index("--request") + 1])
    request = json.loads(request_path.read_text(encoding="utf-8"))
    # An unknown voice id falls back to the keyless default for the language.
    assert request["voice"] == "google-vi" and request["language"] == "vi"
    assert request["product"]["price"] == 199000.0
    assert Path(request["portrait"]).is_file()
    assert studio_videos.get_video(job.id) == job
    assert [v.id for v in studio_videos.list_videos()] == [job.id]


def test_only_one_render_at_a_time(spawned: list[list[str]]) -> None:
    studio_videos.start_video(_model(_portrait_url()), _product())
    with pytest.raises(studio_videos.VideoBusyError):
        studio_videos.start_video(_model(_portrait_url()), _product())
    assert len(spawned) == 1


def test_a_model_without_a_portrait_is_refused(spawned: list[list[str]]) -> None:
    with pytest.raises(studio_videos.VideoUnavailableError):
        studio_videos.start_video(_model(None), _product())
    assert spawned == [] and studio_videos.list_videos() == []


def test_ready_jobs_expose_their_files_and_can_be_deleted(spawned: list[list[str]]) -> None:
    job = studio_videos.start_video(_model(_portrait_url()), _product())
    out = studio_videos.videos_dir() / job.id
    with pytest.raises(studio_videos.VideoBusyError):
        studio_videos.delete_video(job.id)  # still rendering

    (out / "video.mp4").write_bytes(b"mp4")
    (out / "thumb.jpg").write_bytes(b"jpg")
    (out / "status.json").write_text(json.dumps({"status": "ready", "progress": 1.0}))
    ready = studio_videos.get_video(job.id)
    assert ready is not None and ready.status == "ready" and ready.has_video and ready.has_thumb
    assert studio_videos.video_file(job.id) == out / "video.mp4"

    assert studio_videos.delete_video(job.id)
    assert studio_videos.get_video(job.id) is None and not out.exists()


def test_a_render_that_went_silent_counts_as_failed(spawned: list[list[str]]) -> None:
    job = studio_videos.start_video(_model(_portrait_url()), _product())
    status = studio_videos.videos_dir() / job.id / "status.json"
    old = time.time() - 3600
    os.utime(status, (old, old))

    stale = studio_videos.get_video(job.id)
    assert stale is not None and stale.status == "failed" and stale.error
    # A dead render no longer blocks the next one.
    studio_videos.start_video(_model(_portrait_url()), _product())


@pytest.mark.parametrize("bad", ["", "..", "../x", "a/b", "abc"])
def test_unknown_or_malformed_ids_resolve_to_nothing(bad: str) -> None:
    assert studio_videos.get_video(bad) is None
    assert studio_videos.video_file(bad) is None
    assert not studio_videos.delete_video(bad)
