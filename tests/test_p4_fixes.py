"""Unit tests for Phase 4 (P4) implementations:
- PlayoutEngine v2 (Event-driven playout, priority insertion queue, loop, pause/resume)
- Fault tolerance (Missing MP4 triggers filler fallback without stopping session, §6 P4 acceptance criteria)
- PlayoutReporter (Post-session Markdown & JSON summary report generation)
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.playout.engine import PlayoutEngine, PlayoutItem, PlayoutSessionStats, PlayoutState
from app.playout.reporter import PlayoutReporter


# =============================================================================
# 1. PLAYOUT ENGINE V2 TESTS
# =============================================================================
class TestPlayoutEngine:
    def test_engine_initialization_and_empty_playlist(self) -> None:
        engine = PlayoutEngine(playlist=[])
        assert engine.state == PlayoutState.IDLE
        ok = engine.start()
        assert ok is False
        assert engine.state == PlayoutState.ERROR

    def test_engine_lifecycle_start_pause_resume_stop(self, tmp_path: Path) -> None:
        video_1 = tmp_path / "v1.mp4"
        video_1.write_bytes(b"\x00" * 2000)

        items = [PlayoutItem(title="Sản phẩm 1", video_path=str(video_1))]
        mock_obs = MagicMock()
        mock_obs.is_connected.return_value = True

        engine = PlayoutEngine(obs_client=mock_obs, playlist=items)
        assert engine.start() is True
        assert engine.state == PlayoutState.RUNNING

        # Pause
        assert engine.pause() is True
        assert engine.state == PlayoutState.PAUSED

        # Resume
        assert engine.resume() is True
        assert engine.state == PlayoutState.RUNNING

        # Stop
        engine.stop()
        assert engine.state == PlayoutState.STOPPED

    def test_engine_loops_playlist_and_increments_rounds(self, tmp_path: Path) -> None:
        v1 = tmp_path / "v1.mp4"
        v2 = tmp_path / "v2.mp4"
        v1.write_bytes(b"\x00" * 2000)
        v2.write_bytes(b"\x00" * 2000)

        items = [
            PlayoutItem(title="Item 1", video_path=str(v1)),
            PlayoutItem(title="Item 2", video_path=str(v2)),
        ]
        engine = PlayoutEngine(playlist=items, loop_playlist=True)
        engine.start()

        assert engine.current_item.title == "Item 1"

        # Item 1 finishes
        engine.on_media_input_playback_ended()
        assert engine.current_item.title == "Item 2"

        # Item 2 finishes -> loops back to Item 1, increments rounds
        engine.on_media_input_playback_ended()
        assert engine.current_item.title == "Item 1"
        assert engine.stats.rounds_completed == 1

    def test_priority_insertion_queue_plays_first(self, tmp_path: Path) -> None:
        v1 = tmp_path / "v1.mp4"
        v1.write_bytes(b"\x00" * 2000)
        reply_clip = tmp_path / "reply.mp4"
        reply_clip.write_bytes(b"\x00" * 2000)

        items = [PlayoutItem(title="Item 1", video_path=str(v1))]
        engine = PlayoutEngine(playlist=items, loop_playlist=True)
        engine.start()

        # Queue a comment reply priority clip
        engine.insert_priority_clip(reply_clip, title="Trả lời bình luận của khách")

        # When current item finishes, the inserted clip must play next!
        engine.on_media_input_playback_ended()
        assert engine.current_item.title == "Trả lời bình luận của khách"
        assert engine.current_item.is_insertion is True
        assert engine.stats.insertions_played == 1

    def test_engine_recovers_from_missing_file_without_stopping(self, tmp_path: Path) -> None:
        """Requirement §6 P4: Missing file must recover smoothly via filler without stopping stream."""
        missing_video = tmp_path / "missing_file.mp4"  # Does NOT exist
        valid_video = tmp_path / "valid.mp4"
        valid_video.write_bytes(b"\x00" * 2000)

        items = [
            PlayoutItem(title="Sản phẩm bị lỗi file", video_path=str(missing_video)),
            PlayoutItem(title="Sản phẩm hợp lệ", video_path=str(valid_video)),
        ]

        engine = PlayoutEngine(playlist=items, use_filler_fallback=True)
        ok = engine.start()

        # Engine must NOT crash; state must stay RUNNING
        assert ok is True
        assert engine.state == PlayoutState.RUNNING
        assert engine.stats.errors_recovered >= 1
        # Fallback filler was played
        assert engine.stats.filler_plays >= 1
        assert "Standby" in engine.current_item.title


# =============================================================================
# 2. PLAYOUT REPORTER TESTS
# =============================================================================
class TestPlayoutReporter:
    def test_reporter_generates_valid_dict_and_markdown(self) -> None:
        reporter = PlayoutReporter()
        stats = PlayoutSessionStats(
            start_time=time.time() - 3665,  # ~1 hour 1 minute ago
            end_time=time.time(),
            items_presented=12,
            rounds_completed=2,
            insertions_played=3,
            errors_recovered=1,
            filler_plays=1,
            dropped_frames=0,
        )

        data = reporter.generate_report_dict(stats, session_name="Phiên Live Sáng Chủ Nhật")
        assert data["items_presented"] == 12
        assert data["rounds_completed"] == 2
        assert data["insertions_played"] == 3
        assert "phút" in data["duration_formatted"]

        md = reporter.generate_markdown_report(stats, session_name="Phiên Live Sáng Chủ Nhật")
        assert "# 📊 Báo Cáo Kết Quả Phiên Livestream Tự Động" in md
        assert "Phiên Live Sáng Chủ Nhật" in md
        assert "`12`" in md
        assert "`2`" in md

    def test_reporter_exports_report_files_to_disk(self, tmp_path: Path) -> None:
        reporter = PlayoutReporter(reports_dir=tmp_path)
        stats = PlayoutSessionStats(
            start_time=time.time() - 120,
            end_time=time.time(),
            items_presented=4,
            rounds_completed=1,
        )

        md_path = reporter.export_report_to_disk(stats, session_name="test_session")
        assert md_path.exists()
        assert md_path.suffix == ".md"

        # Check JSON companion file
        json_path = md_path.with_suffix(".json")
        assert json_path.exists()
        assert "items_presented" in json_path.read_text(encoding="utf-8")
