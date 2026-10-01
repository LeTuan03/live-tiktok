"""Application bootstrap — dependency checking and initialization."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from loguru import logger


class DependencyCheckResult:
    """Result of a single dependency check."""

    def __init__(
        self,
        name: str,
        found: bool,
        version: str = "",
        path: str = "",
        install_hint: str = "",
        required: bool = True,
    ) -> None:
        self.name = name
        self.found = found
        self.version = version
        self.path = path
        self.install_hint = install_hint
        self.required = required

    @property
    def status_text(self) -> str:
        if self.found:
            return f"✅ {self.name}: {self.version}" + (f" ({self.path})" if self.path else "")
        marker = "❌" if self.required else "⚠️"
        return f"{marker} {self.name}: Không tìm thấy — {self.install_hint}"


def check_python_version() -> DependencyCheckResult:
    """Check Python version meets minimum requirement (3.12+)."""
    version = sys.version.split()[0]
    major, minor = sys.version_info[:2]
    found = major >= 3 and minor >= 12
    return DependencyCheckResult(
        name="Python",
        found=found,
        version=version,
        path=sys.executable,
        install_hint="Cần Python 3.12 trở lên. Tải tại https://python.org",
    )


def check_ffmpeg() -> DependencyCheckResult:
    """Check if FFmpeg is available."""
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        try:
            result = subprocess.run(
                ["ffmpeg", "-version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            version_line = result.stdout.split("\n")[0] if result.stdout else "unknown"
            return DependencyCheckResult(
                name="FFmpeg",
                found=True,
                version=version_line,
                path=ffmpeg_path,
            )
        except Exception:
            pass

    return DependencyCheckResult(
        name="FFmpeg",
        found=False,
        install_hint="Tải FFmpeg tại https://ffmpeg.org/download.html và thêm vào PATH",
        required=False,
    )


def check_ffprobe() -> DependencyCheckResult:
    """Check if FFprobe is available."""
    ffprobe_path = shutil.which("ffprobe")
    if ffprobe_path:
        return DependencyCheckResult(
            name="FFprobe",
            found=True,
            version="bundled with FFmpeg",
            path=ffprobe_path,
        )
    return DependencyCheckResult(
        name="FFprobe",
        found=False,
        install_hint="FFprobe đi kèm với FFmpeg",
        required=False,
    )


def check_obs_running() -> DependencyCheckResult:
    """Check if OBS Studio is currently running (not required at startup)."""
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq obs64.exe"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            found = "obs64.exe" in result.stdout
        else:
            found = False

        return DependencyCheckResult(
            name="OBS Studio",
            found=found,
            version="running" if found else "not running",
            install_hint="Tải OBS tại https://obsproject.com — có thể khởi động sau",
            required=False,
        )
    except Exception:
        return DependencyCheckResult(
            name="OBS Studio",
            found=False,
            install_hint="Tải OBS tại https://obsproject.com",
            required=False,
        )


def run_all_checks() -> list[DependencyCheckResult]:
    """Run all dependency checks and return results."""
    checks = [
        check_python_version(),
        check_ffmpeg(),
        check_ffprobe(),
        check_obs_running(),
    ]

    for check in checks:
        logger.info(check.status_text)

    return checks


def check_critical_failures(results: list[DependencyCheckResult]) -> list[DependencyCheckResult]:
    """Return only critical (required) failures."""
    return [r for r in results if r.required and not r.found]


def initialize_app_directories(data_dir: Path) -> None:
    """Create all necessary application directories."""
    dirs = [
        data_dir,
        data_dir / "logs",
        data_dir / "media",
        data_dir / "tts_cache",
        data_dir / "exports",
        data_dir / "backups",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    logger.info("Application directories initialized: {}", data_dir)
