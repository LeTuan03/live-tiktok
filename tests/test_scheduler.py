"""Orchestrator tests: pause/resume/stop, error limits, OBS routing, entry building."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from app.services.scheduler_service import (
    LiveOrchestrator,
    OrchestratorConfig,
    PlaylistEntry,
    RunState,
    build_entries_from_playlist,
)


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


def _wait(predicate, timeout=5.0):
    """Poll ``predicate`` while pumping the Qt event loop (queued signals need it)."""
    from PySide6.QtCore import QCoreApplication

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    QCoreApplication.processEvents()
    return predicate()


def _entries(n=3, seconds=0.5):
    return [PlaylistEntry(item_id=i, product_id=i, product_name=f"SP{i}", duration_seconds=seconds) for i in range(1, n + 1)]


def _config(entries, **kw):
    base = dict(playlist_id=None, playlist_name="test", entries=entries, loop=False, loop_count=1, gap_seconds=0.0,
                default_item_seconds=0.3, max_consecutive_errors=2)
    base.update(kw)
    return OrchestratorConfig(**base)


def test_runs_all_items_and_completes(qapp, db):
    o = LiveOrchestrator(_config(_entries(3, 0.3)), obs_client=None)
    finished = []
    started = []
    o.item_started.connect(lambda i, e: started.append(e.product_name))
    o.finished_run.connect(finished.append)
    o.start()
    assert _wait(lambda: bool(finished), 10)
    o.wait(2000)
    assert finished == [RunState.COMPLETED.value]
    assert started == ["SP1", "SP2", "SP3"]


def test_pause_resume_stop(qapp, db):
    o = LiveOrchestrator(_config(_entries(5, 1.0)), obs_client=None)
    finished = []
    o.finished_run.connect(finished.append)
    o.start()
    assert _wait(lambda: o.state == RunState.RUNNING)
    o.pause()
    assert o.state == RunState.PAUSED
    idx_before = o.current_index
    time.sleep(1.3)
    assert o.current_index == idx_before  # no progress while paused
    o.resume()
    assert o.state == RunState.RUNNING
    o.stop()
    assert _wait(lambda: bool(finished), 10)
    o.wait(2000)
    assert finished == [RunState.STOPPED.value]
    assert o.state == RunState.STOPPED


def test_skip_moves_to_next_item(qapp, db):
    o = LiveOrchestrator(_config(_entries(2, 5.0)), obs_client=None)
    statuses = []
    finished = []
    o.item_finished.connect(lambda i, s: statuses.append(s))
    o.finished_run.connect(finished.append)
    o.start()
    assert _wait(lambda: o.current_index == 0)
    time.sleep(0.2)
    o.skip_current()
    assert _wait(lambda: o.current_index == 1, 5)
    o.stop()
    assert _wait(lambda: bool(finished), 10)
    o.wait(2000)
    assert statuses[0] == "skipped"


def test_consecutive_errors_stop_the_run(qapp, db):
    # Items whose video file is missing and have no audio → "failed"
    entries = [PlaylistEntry(item_id=i, product_id=i, product_name=f"X{i}", video_path=r"C:\nope\missing.mp4", duration_seconds=0.1) for i in range(5)]
    o = LiveOrchestrator(_config(entries, max_consecutive_errors=2), obs_client=None)
    finished = []
    o.finished_run.connect(finished.append)
    o.start()
    assert _wait(lambda: bool(finished), 10)
    o.wait(2000)
    assert finished == [RunState.ERROR.value]


def test_empty_playlist_errors_immediately(qapp, db):
    o = LiveOrchestrator(_config([]), obs_client=None)
    finished = []
    o.finished_run.connect(finished.append)
    o.start()
    assert _wait(lambda: bool(finished), 5)
    o.wait(2000)
    assert finished == [RunState.ERROR.value]


def test_obs_media_and_text_are_set_per_item(qapp, db, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0")
    obs = MagicMock()
    obs.set_media_source_file.return_value = True
    obs.set_current_scene.return_value = True
    entries = [PlaylistEntry(item_id=1, product_id=1, product_name="Cam", video_path=str(video), price_text="Giá 735.000đ", duration_seconds=0.2)]
    cfg = _config(entries, use_obs=True, obs_scene="LiveFlow AI", obs_video_source="LiveFlow Video",
                  obs_audio_source="LiveFlow Audio", obs_image_source="LiveFlow Image", obs_text_source="LiveFlow Text")
    o = LiveOrchestrator(cfg, obs_client=obs)
    finished = []
    o.finished_run.connect(finished.append)
    o.start()
    assert _wait(lambda: bool(finished), 10)
    o.wait(2000)
    obs.set_current_scene.assert_called_with("LiveFlow AI")
    obs.set_media_source_file.assert_any_call("LiveFlow Video", str(video), loop=True)
    obs.set_text.assert_any_call("LiveFlow Text", "Cam\nGiá 735.000đ")


def test_local_audio_requested_when_no_obs(qapp, db, tmp_path):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"\xff\xfb" * 600)  # ~0.2s by size estimate
    entries = [PlaylistEntry(item_id=1, product_id=1, product_name="A", audio_path=str(audio), duration_seconds=0.2)]
    o = LiveOrchestrator(_config(entries), obs_client=None)
    played = []
    finished = []
    o.play_audio_requested.connect(played.append)
    o.finished_run.connect(finished.append)
    o.start()
    assert _wait(lambda: bool(finished), 10)
    o.wait(2000)
    assert played == [str(audio)]


def test_build_entries_resolves_video_image_audio(session, tmp_path):
    from PIL import Image

    from app.database.repositories.playlist_repo import PlaylistRepository
    from app.database.repositories.product_repo import ProductRepository
    from app.database.repositories.script_repo import ScriptRepository
    from app.database.repositories.voice_repo import VoiceRepository
    from app.services.media_service import MediaService

    img = tmp_path / "p.png"
    Image.new("RGB", (10, 10)).save(img)
    audio = tmp_path / "s.mp3"
    audio.write_bytes(b"\xff\xfb" * 100)

    product = ProductRepository().create({"name": "Camera", "price": 900000, "promo_price": 735000})
    media = MediaService().import_file(img)
    MediaService().link_to_product(media.id, product.id, is_primary=True)
    script = ScriptRepository(session).create(product_id=product.id, title="KB", content="Chào cả nhà")
    VoiceRepository(session).create_generated_audio(script.id, str(audio), "hash", duration_seconds=12.0)

    repo = PlaylistRepository(session)
    pl = repo.create_playlist("x")
    repo.add_item(pl.id, product_id=product.id)
    entries = build_entries_from_playlist(repo.get_by_id(pl.id, fresh=True))
    assert len(entries) == 1
    e = entries[0]
    assert e.image_path == str(img) and e.video_path is None
    assert e.audio_path == str(audio) and e.audio_duration == 12.0
    assert "735.000" in e.price_text and "900.000" in e.price_text
    assert e.script_title == "KB"
