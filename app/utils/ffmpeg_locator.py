"""Locate ffmpeg/ffprobe executables on Windows without requiring PATH edits."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Optional

from app.config.settings import get_project_root


def _candidate_dirs(extra_dir: str = "") -> list[Path]:
    root = get_project_root()
    local = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    dirs: list[Path] = []
    if extra_dir:
        dirs.append(Path(extra_dir))
        dirs.append(Path(extra_dir) / "bin")
    dirs += [
        root / "tools" / "ffmpeg" / "bin",
        root / "tools" / "ffmpeg",
        root / "ffmpeg" / "bin",
        Path(r"C:\ffmpeg\bin"),
        Path(program_files) / "ffmpeg" / "bin",
        Path(local) / "Microsoft" / "WinGet" / "Links" if local else Path(),
        Path(local) / "Programs" / "ffmpeg" / "bin" if local else Path(),
    ]
    # tools/ffmpeg/<extracted-folder>/bin (gyan.dev zip layout)
    tools = root / "tools" / "ffmpeg"
    if tools.exists():
        for child in tools.iterdir():
            if child.is_dir():
                dirs.append(child / "bin")
    return [d for d in dirs if str(d) not in ("", ".")]


def find_executable(name: str, extra_dir: str = "") -> Optional[str]:
    """Return the full path of ``ffmpeg``/``ffprobe`` or None."""
    exe = f"{name}.exe" if sys.platform == "win32" and not name.endswith(".exe") else name
    for d in _candidate_dirs(extra_dir):
        p = d / exe
        if p.is_file():
            return str(p)
    found = shutil.which(name)
    return found


def find_ffmpeg(extra_dir: str = "") -> Optional[str]:
    return find_executable("ffmpeg", extra_dir)


def find_ffprobe(extra_dir: str = "") -> Optional[str]:
    return find_executable("ffprobe", extra_dir)


def apply_to_settings(extra_dir: str = "") -> tuple[Optional[str], Optional[str]]:
    """Resolve both binaries and store the paths on the settings singleton."""
    from app.config.settings import get_settings

    settings = get_settings()
    ffmpeg = find_ffmpeg(extra_dir)
    ffprobe = find_ffprobe(extra_dir)
    if ffmpeg:
        settings.ffmpeg_path = ffmpeg
    if ffprobe:
        settings.ffprobe_path = ffprobe
    return ffmpeg, ffprobe
