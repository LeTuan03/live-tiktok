"""Application bootstrap — dependency checking and initialization."""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

from loguru import logger

from app.utils.ffmpeg_locator import find_ffmpeg, find_ffprobe

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


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
    found = sys.version_info >= (3, 12)
    return DependencyCheckResult(
        name="Python",
        found=found,
        version=version,
        path=sys.executable,
        install_hint="Cần Python 3.12 trở lên. Tải tại https://python.org",
    )


def check_python_package(module: str, pip_name: str, required: bool = True) -> DependencyCheckResult:
    try:
        mod = importlib.import_module(module)
        version = getattr(mod, "__version__", "") or "installed"
        return DependencyCheckResult(name=pip_name, found=True, version=version, required=required)
    except Exception:
        return DependencyCheckResult(
            name=pip_name,
            found=False,
            install_hint=f"pip install {pip_name} (hoặc chạy INSTALL.bat)",
            required=required,
        )


def check_ffmpeg() -> DependencyCheckResult:
    """Check if FFmpeg is available (PATH, tools/ffmpeg, common install dirs)."""
    ffmpeg_path = find_ffmpeg()
    if ffmpeg_path:
        try:
            result = subprocess.run(
                [ffmpeg_path, "-version"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=_NO_WINDOW,
            )
            version_line = result.stdout.split("\n")[0] if result.stdout else "unknown"
            return DependencyCheckResult(name="FFmpeg", found=True, version=version_line[:60], path=ffmpeg_path)
        except Exception:
            pass
    return DependencyCheckResult(
        name="FFmpeg",
        found=False,
        install_hint="Chạy scripts\\download_ffmpeg.ps1 hoặc 'winget install Gyan.FFmpeg' (chỉ cần để đọc metadata video)",
        required=False,
    )


def check_ffprobe() -> DependencyCheckResult:
    ffprobe_path = find_ffprobe()
    if ffprobe_path:
        return DependencyCheckResult(name="FFprobe", found=True, version="đi kèm FFmpeg", path=ffprobe_path)
    return DependencyCheckResult(
        name="FFprobe", found=False, install_hint="FFprobe đi kèm với FFmpeg", required=False
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
                creationflags=_NO_WINDOW,
            )
            found = "obs64.exe" in result.stdout
        else:
            found = False
        return DependencyCheckResult(
            name="OBS Studio",
            found=found,
            version="đang chạy" if found else "chưa chạy",
            install_hint="Tải OBS tại https://obsproject.com — có thể mở sau khi khởi động LiveFlow",
            required=False,
        )
    except Exception:
        return DependencyCheckResult(
            name="OBS Studio", found=False, install_hint="Tải OBS tại https://obsproject.com", required=False
        )


def run_all_checks() -> list[DependencyCheckResult]:
    """Run all dependency checks and return results."""
    checks = [
        check_python_version(),
        check_python_package("PySide6", "PySide6"),
        check_python_package("sqlalchemy", "SQLAlchemy"),
        check_python_package("pydantic_settings", "pydantic-settings"),
        check_python_package("aiohttp", "aiohttp"),
        check_python_package("edge_tts", "edge-tts"),
        check_python_package("obsws_python", "obsws-python"),
        check_python_package("bs4", "beautifulsoup4", required=False),
        check_python_package("keyring", "keyring", required=False),
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
    for d in (
        data_dir,
        data_dir / "logs",
        data_dir / "media",
        data_dir / "audio",
        data_dir / "tts_cache",
        data_dir / "exports",
        data_dir / "backups",
    ):
        d.mkdir(parents=True, exist_ok=True)
    logger.info("Application directories initialized: {}", data_dir)
