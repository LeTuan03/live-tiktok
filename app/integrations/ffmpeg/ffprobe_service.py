"""FFmpeg/FFprobe integration for media metadata extraction."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from app.config.settings import get_settings


class FFProbeResult:
    """Parsed ffprobe output for a media file."""

    def __init__(self, raw: dict) -> None:
        self.raw = raw
        self._format = raw.get("format", {})
        self._streams = raw.get("streams", [])
        self._video_stream = next(
            (s for s in self._streams if s.get("codec_type") == "video"), None
        )
        self._audio_stream = next(
            (s for s in self._streams if s.get("codec_type") == "audio"), None
        )

    @property
    def duration(self) -> Optional[float]:
        d = self._format.get("duration")
        return float(d) if d else None

    @property
    def width(self) -> Optional[int]:
        if self._video_stream:
            return self._video_stream.get("width")
        return None

    @property
    def height(self) -> Optional[int]:
        if self._video_stream:
            return self._video_stream.get("height")
        return None

    @property
    def codec(self) -> Optional[str]:
        if self._video_stream:
            return self._video_stream.get("codec_name")
        if self._audio_stream:
            return self._audio_stream.get("codec_name")
        return None

    @property
    def format_name(self) -> Optional[str]:
        return self._format.get("format_name")

    @property
    def file_size(self) -> Optional[int]:
        s = self._format.get("size")
        return int(s) if s else None

    @property
    def bitrate(self) -> Optional[int]:
        b = self._format.get("bit_rate")
        return int(b) if b else None

    @property
    def is_video(self) -> bool:
        return self._video_stream is not None

    @property
    def is_audio(self) -> bool:
        return self._audio_stream is not None and not self.is_video

    @property
    def media_type(self) -> str:
        return "video" if self.is_video else "audio"

    def to_dict(self) -> dict[str, Any]:
        return {
            "duration_seconds": self.duration,
            "width": self.width,
            "height": self.height,
            "codec": self.codec,
            "format_name": self.format_name,
            "file_size_bytes": self.file_size,
            "bitrate": self.bitrate,
            "media_type": self.media_type,
        }


class FFProbeService:
    """Service for extracting media metadata using ffprobe."""

    def __init__(self) -> None:
        self._settings = get_settings()

    @property
    def _ffprobe_path(self) -> str:
        return self._settings.ffprobe_path or "ffprobe"

    @property
    def _ffmpeg_path(self) -> str:
        return self._settings.ffmpeg_path or "ffmpeg"

    def is_available(self) -> bool:
        """True when the ffprobe binary can be found."""
        import shutil
        from pathlib import Path as _P

        p = self._ffprobe_path
        return _P(p).is_file() or shutil.which(p) is not None

    def probe(self, file_path: str | Path) -> Optional[FFProbeResult]:
        """Run ffprobe on a file and return parsed metadata.

        Args:
            file_path: Path to the media file.

        Returns:
            FFProbeResult with metadata, or None if probe failed.
        """
        file_path = Path(file_path)
        if not file_path.exists():
            logger.warning("File not found for probe: {}", file_path)
            return None

        try:
            result = subprocess.run(
                [
                    self._ffprobe_path,
                    "-v", "quiet",
                    "-print_format", "json",
                    "-show_format",
                    "-show_streams",
                    str(file_path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )

            if result.returncode != 0:
                logger.error("ffprobe failed for {}: {}", file_path, result.stderr)
                return None

            data = json.loads(result.stdout)
            return FFProbeResult(data)

        except subprocess.TimeoutExpired:
            logger.error("ffprobe timeout for: {}", file_path)
            return None
        except json.JSONDecodeError:
            logger.error("ffprobe output not valid JSON for: {}", file_path)
            return None
        except FileNotFoundError:
            logger.error("ffprobe not found at: {}", self._ffprobe_path)
            return None
        except Exception:
            logger.exception("ffprobe error for: {}", file_path)
            return None

    def is_valid_media(self, file_path: str | Path) -> bool:
        """Check if a file is a valid media file."""
        result = self.probe(file_path)
        return result is not None

    def get_duration(self, file_path: str | Path) -> Optional[float]:
        """Get the duration of a media file in seconds."""
        result = self.probe(file_path)
        return result.duration if result else None

    def format_duration(self, seconds: Optional[float]) -> str:
        """Format seconds into HH:MM:SS string."""
        if seconds is None:
            return "—"
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def format_file_size(self, size_bytes: Optional[int]) -> str:
        """Format bytes into human-readable string."""
        if size_bytes is None:
            return "—"
        if size_bytes < 1024:
            return f"{size_bytes} B"
        if size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        if size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

    def format_resolution(self, width: Optional[int], height: Optional[int]) -> str:
        """Format resolution as WxH string."""
        if width and height:
            return f"{width}×{height}"
        return "—"
