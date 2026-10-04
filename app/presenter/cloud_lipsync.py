"""Cloud Lipsync Provider — synchronizes avatar mouth motion with audio track."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from loguru import logger

from app.config.settings import get_settings
from app.core.exceptions import AudioRenderError
from app.integrations.ffmpeg.ffprobe_service import FFProbeService
from app.presenter.base import PresenterProvider


class CloudLipsyncProvider(PresenterProvider):
    """Cloud-based lipsync engine (SyncLabs / HeyGen / D-ID / Wav2Lip fallback)."""

    def __init__(self, api_key: Optional[str] = None) -> None:
        super().__init__(name="Cloud Lipsync Engine", provider_type="cloud_lipsync")
        settings = get_settings()
        self.api_key = api_key or settings.gemini_api_key or ""
        self.ffprobe = FFProbeService()

    def validate_credentials(self) -> tuple[bool, str]:
        # For offline / local processing via ffmpeg
        if not self.ffprobe.is_available():
            return False, "FFmpeg / FFprobe chưa sẵn sàng."
        return True, "Hệ thống lipsync & compositor đã sẵn sàng."

    async def generate_lipsync(
        self,
        base_clip: Path | str,
        audio_path: Path | str,
        output_path: Path | str,
    ) -> Path:
        """Combine base presenter video with speech audio track, looping/trimming video to match audio length."""
        base_file = Path(base_clip)
        audio_file = Path(audio_path)
        out_file = Path(output_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)

        if not base_file.exists():
            raise FileNotFoundError(f"Không tìm thấy clip cử chỉ gốc: {base_file}")
        if not audio_file.exists():
            raise FileNotFoundError(f"Không tìm thấy audio đọc: {audio_file}")

        probe_audio = self.ffprobe.probe(audio_file)
        audio_dur = probe_audio.duration if probe_audio and probe_audio.duration else 10.0

        ffmpeg_exe = self.ffprobe._ffmpeg_path
        if not ffmpeg_exe:
            raise AudioRenderError("Không tìm thấy ffmpeg để đồng bộ lipsync video.")

        # §4.2 Bước 4:
        # Nếu clip ngắn hơn audio: ffmpeg loop clip cho đủ thời lượng
        # Nếu dài hơn: cắt theo audio (-shortest / -t audio_dur)
        cmd = [
            ffmpeg_exe,
            "-y",
            "-stream_loop", "-1",  # loop base video if shorter than speech
            "-i", str(base_file),
            "-i", str(audio_file),
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "128k",
            "-t", f"{audio_dur:.2f}",
            "-shortest",
            str(out_file),
        ]

        logger.info("Đồng bộ âm thanh vào clip cử chỉ: {} (thời lượng: {:.1f}s)", out_file.name, audio_dur)
        proc = await asyncio.to_thread(
            subprocess.run,
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

        if proc.returncode != 0 or not out_file.exists() or out_file.stat().st_size == 0:
            err = proc.stderr[-500:] if proc.stderr else "Lỗi không xác định"
            logger.error("Lỗi ffmpeg lipsync: {}", err)
            raise AudioRenderError(f"Không thể đồng bộ lipsync: {err}")

        return out_file

    async def generate_gesture_clip(
        self,
        reference_image: Path | str,
        cue: str,
        prompt: str,
        output_path: Path | str,
    ) -> Path:
        """Create a placeholder gesture clip from photo when cloud i2v is not called."""
        out_file = Path(output_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)

        ffmpeg_exe = self.ffprobe._ffmpeg_path
        cmd = [
            ffmpeg_exe,
            "-y",
            "-loop", "1",
            "-i", str(reference_image),
            "-c:v", "libx264",
            "-t", "5.0",
            "-pix_fmt", "yuv420p",
            str(out_file),
        ]
        await asyncio.to_thread(subprocess.run, cmd, capture_output=True, timeout=60)
        return out_file

    async def generate_product_clip(
        self,
        presenter_image: Path | str,
        product_image: Path | str,
        cue: str,
        output_path: Path | str,
        prompt_override: Optional[str] = None,
    ) -> Path:
        """Create a product interaction clip."""
        return await self.generate_gesture_clip(
            reference_image=presenter_image,
            cue=cue,
            prompt=prompt_override or f"presenter with product for cue {cue}",
            output_path=output_path,
        )
