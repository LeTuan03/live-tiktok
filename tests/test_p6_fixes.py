"""Unit and integration tests for Phase 6 — Packaging, Diagnostics & Deployment Checklist (§6 P6)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app.services.log_exporter import LogExporter, sanitize_log_content
from tools.doctor import LiveFlowDoctor


class TestLiveFlowDoctor:
    """Acceptance criteria P6: doctor script checks environment (OBS, fonts, disk, network, GPU, FFmpeg)."""

    def test_doctor_runs_all_checks_without_exception(self) -> None:
        doctor = LiveFlowDoctor()
        results = doctor.run_all_checks()
        assert len(results) >= 6

        names = [r.name for r in results]
        assert "Python Runtime" in names
        assert "FFmpeg / FFprobe" in names
        assert "Phông chữ Tiếng Việt" in names
        assert "Dung lượng Ổ đĩa" in names
        assert "Kết nối Mạng" in names
        assert "Cấu hình Bộ mã hóa Video" in names

    def test_doctor_python_check_passes_on_current_env(self) -> None:
        doctor = LiveFlowDoctor()
        res = doctor.check_python()
        assert res.status == "PASS"

    def test_doctor_fonts_check(self) -> None:
        doctor = LiveFlowDoctor()
        res = doctor.check_fonts()
        assert res.status in ("PASS", "WARN")

    def test_doctor_disk_check(self) -> None:
        doctor = LiveFlowDoctor()
        res = doctor.check_disk_space()
        assert res.status in ("PASS", "WARN")


class TestLogExporter:
    """Acceptance criteria P6: Log rotation and export log package for support."""

    def test_log_sanitizer_masks_credentials(self) -> None:
        raw_log = (
            "Connecting with stream_key=live_1234567890abcdef1234567890\n"
            "Using GEMINI key: AIzaSyD9876543210zyxwvutsrqponmlkjihgfed\n"
            "OBS password: secretpassword123\n"
        )
        clean = sanitize_log_content(raw_log)
        assert "live_1234567890abcdef1234567890" not in clean
        assert "AIzaSyD9876543210zyxwvutsrqponmlkjihgfed" not in clean
        assert "secretpassword123" not in clean
        assert "***REDACTED***" in clean

    def test_export_package_creates_valid_zip(self, tmp_path: Path) -> None:
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        test_log = logs_dir / "app.log"
        test_log.write_text("2026-10-04 INFO Starting session token=xyz12345", encoding="utf-8")

        export_dir = tmp_path / "exports"
        exporter = LogExporter(logs_dir=logs_dir, export_dir=export_dir)
        zip_path = exporter.export_package()

        assert zip_path.exists()
        assert zip_path.stat().st_size > 0

        # Verify ZIP contains doctor report and sanitized logs
        with zipfile.ZipFile(zip_path, "r") as zf:
            file_names = zf.namelist()
            assert "doctor_report.txt" in file_names
            assert "logs/app.log" in file_names
            content = zf.read("logs/app.log").decode("utf-8")
            assert "xyz12345" not in content


class TestOperatorGuideAndPackaging:
    """Acceptance criteria P6: 1-page operator guide and installer config."""

    def test_operator_guide_exists_and_contains_checklist(self) -> None:
        guide_path = Path("OPERATOR_GUIDE.md")
        assert guide_path.exists()
        content = guide_path.read_text(encoding="utf-8")
        assert "CHECKLIST" in content
        assert "OBS Studio" in content
        assert "doctor.bat" in content
        assert "1080 x 1920" in content

    def test_installer_script_exists(self) -> None:
        iss_path = Path("installer/liveflow_setup.iss")
        assert iss_path.exists()
        content = iss_path.read_text(encoding="utf-8")
        assert "LiveFlow AI Studio" in content
        assert "doctor.bat" in content
