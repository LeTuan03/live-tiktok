"""Cloud Lipsync Provider — synchronizes avatar mouth motion with audio track.

Two modes:
1. Cloud lipsync (Sync.so / SyncLabs API): Sends video + audio → receives lipsync'd video
   where the presenter's mouth matches the speech. This is the REAL lipsync.
2. Local ffmpeg merge (fallback): Combines video loop + audio track — mouth does NOT match.
   Still useful as it produces valid MP4 with correct timing.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Optional

import aiohttp
from loguru import logger

from app.config.settings import get_settings
from app.core.exceptions import AudioRenderError
from app.integrations.ffmpeg.ffprobe_service import FFProbeService
from app.presenter.base import PresenterProvider

# Sync.so API constants
_SYNC_API_BASE = "https://api.synclabs.so"
_SYNC_POLL_INTERVAL = 5
_SYNC_MAX_POLLS = 60  # 60 × 5s = 300s max


class CloudLipsyncProvider(PresenterProvider):
    """Cloud-based lipsync engine with Sync.so primary and ffmpeg fallback.

    Priority:
    1. Sync.so / SyncLabs (real lipsync — mouth matches speech)
    2. ffmpeg merge (no lipsync — just combines video + audio with loop/trim)
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        sync_api_key: Optional[str] = None,
    ) -> None:
        super().__init__(name="Cloud Lipsync Engine", provider_type="cloud_lipsync")
        settings = get_settings()
        self.api_key = api_key or settings.gemini_api_key or ""
        self.sync_api_key = sync_api_key or os.environ.get("LIVEFLOW_SYNC_API_KEY", "")
        self.ffprobe = FFProbeService()

    def validate_credentials(self) -> tuple[bool, str]:
        # Check ffmpeg availability (minimum requirement for fallback)
        if not self.ffprobe.is_available():
            return False, "FFmpeg / FFprobe chưa sẵn sàng."
        if self.sync_api_key:
            return True, "Sync.so lipsync engine + FFmpeg đã sẵn sàng."
        return True, "FFmpeg compositor đã sẵn sàng (chế độ ghép audio, không có lipsync miệng)."

    @property
    def has_real_lipsync(self) -> bool:
        """Whether real mouth-matching lipsync is available (vs just audio merge)."""
        return bool(self.sync_api_key)

    async def generate_lipsync(
        self,
        base_clip: Path | str,
        audio_path: Path | str,
        output_path: Path | str,
    ) -> Path:
        """Combine base presenter video with speech audio track.

        If Sync.so API is configured: real lipsync where mouth matches speech.
        Otherwise: ffmpeg loop/trim merge (timing correct, mouth does NOT match).
        """
        base_file = Path(base_clip)
        audio_file = Path(audio_path)
        out_file = Path(output_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)

        if not base_file.exists():
            raise FileNotFoundError(f"Không tìm thấy clip cử chỉ gốc: {base_file}")
        if not audio_file.exists():
            raise FileNotFoundError(f"Không tìm thấy audio đọc: {audio_file}")

        # Try real lipsync first
        if self.sync_api_key:
            try:
                return await self._lipsync_synclabs(base_file, audio_file, out_file)
            except Exception as e:
                logger.warning("Sync.so lipsync thất bại, chuyển sang ffmpeg fallback: {}", e)

        # Fallback: ffmpeg merge (no mouth matching)
        return await self._lipsync_ffmpeg_merge(base_file, audio_file, out_file)

    async def _lipsync_synclabs(
        self, base_clip: Path, audio_path: Path, out_file: Path
    ) -> Path:
        """Real lipsync using Sync.so / SyncLabs API.

        Process: Upload video + audio → Sync.so matches mouth movements to speech
        → Download resulting video with synced lips.
        """
        headers = {
            "x-api-key": self.sync_api_key,
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=300.0)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            # 1. Upload video file
            logger.info("Uploading video to Sync.so for lipsync...")
            video_url = await self._upload_to_synclabs(session, base_clip, "video")
            audio_url = await self._upload_to_synclabs(session, audio_path, "audio")

            # 2. Create lipsync job
            payload = {
                "videoUrl": video_url,
                "audioUrl": audio_url,
                "synergize": True,
                "model": "sync-1.7.1-beta",
            }
            async with session.post(
                f"{_SYNC_API_BASE}/lipsync", json=payload, headers=headers
            ) as res:
                if res.status not in (200, 201):
                    err = await res.text()
                    raise AudioRenderError(f"Sync.so lipsync lỗi ({res.status}): {err}")
                data = await res.json()
                job_id = data.get("id")

            if not job_id:
                raise AudioRenderError(f"Sync.so không trả về job ID: {data}")
            logger.info("Sync.so lipsync job created: {}", job_id)

            # 3. Poll for completion
            result_url: Optional[str] = None
            for attempt in range(_SYNC_MAX_POLLS):
                await asyncio.sleep(_SYNC_POLL_INTERVAL)
                async with session.get(
                    f"{_SYNC_API_BASE}/lipsync/{job_id}", headers=headers
                ) as poll_res:
                    if poll_res.status == 200:
                        data = await poll_res.json()
                        status = data.get("status")
                        logger.debug("Sync.so poll #{}: status={}", attempt + 1, status)
                        if status == "COMPLETED":
                            result_url = data.get("videoUrl") or data.get("outputUrl")
                            break
                        elif status in ("FAILED", "REJECTED"):
                            raise AudioRenderError(f"Sync.so lipsync thất bại: {data}")

            if not result_url:
                raise AudioRenderError("Hết thời gian chờ Sync.so lipsync.")

            # 4. Download result
            logger.info("Sync.so lipsync hoàn tất, đang tải video...")
            async with session.get(result_url) as dl_res:
                if dl_res.status == 200:
                    content = await dl_res.read()
                    out_file.write_bytes(content)
                    logger.info("Đã tải video lipsync: {} ({:.1f} KB)", out_file.name, len(content) / 1024)
                    return out_file
                raise AudioRenderError(f"Không thể tải video lipsync: HTTP {dl_res.status}")

    async def _upload_to_synclabs(
        self, session: aiohttp.ClientSession, file_path: Path, file_type: str
    ) -> str:
        """Upload file to Sync.so and return the hosted URL."""
        upload_headers = {
            "x-api-key": self.sync_api_key,
        }
        data = aiohttp.FormData()
        content_type = "video/mp4" if file_type == "video" else "audio/mpeg"
        data.add_field("file", file_path.read_bytes(), filename=file_path.name, content_type=content_type)

        async with session.post(
            f"{_SYNC_API_BASE}/assets",
            data=data,
            headers=upload_headers,
        ) as res:
            if res.status not in (200, 201):
                err = await res.text()
                raise AudioRenderError(f"Sync.so upload {file_type} thất bại: {err}")
            result = await res.json()
            url = result.get("url") or result.get("assetUrl")
            if not url:
                raise AudioRenderError(f"Sync.so upload không trả về URL: {result}")
            return url

    async def _lipsync_ffmpeg_merge(
        self, base_clip: Path, audio_path: Path, out_file: Path
    ) -> Path:
        """Fallback: Combine video + audio using ffmpeg (no real mouth matching).

        §4.2 Bước 4:
        - If clip shorter than audio: loop video (ping-pong) to match audio length
        - If clip longer than audio: trim to audio length
        """
        probe_audio = self.ffprobe.probe(audio_path)
        audio_dur = probe_audio.duration if probe_audio and probe_audio.duration else 10.0

        ffmpeg_exe = self.ffprobe._ffmpeg_path
        if not ffmpeg_exe:
            raise AudioRenderError("Không tìm thấy ffmpeg để đồng bộ lipsync video.")

        cmd = [
            ffmpeg_exe,
            "-y",
            "-stream_loop", "-1",  # loop base video if shorter than speech
            "-i", str(base_clip),
            "-i", str(audio_path),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "128k",
            "-t", f"{audio_dur:.2f}",
            "-shortest",
            "-r", "30",
            str(out_file),
        ]

        logger.info(
            "Đồng bộ âm thanh vào clip cử chỉ (ffmpeg merge, không có lipsync miệng): {} (thời lượng: {:.1f}s)",
            out_file.name, audio_dur,
        )
        proc = await asyncio.to_thread(
            subprocess.run,
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

        if proc.returncode != 0 or not out_file.exists() or out_file.stat().st_size == 0:
            err = proc.stderr[-500:] if proc.stderr else "Lỗi không xác định"
            logger.error("Lỗi ffmpeg lipsync merge: {}", err)
            raise AudioRenderError(f"Không thể đồng bộ lipsync: {err}")

        return out_file

    async def generate_gesture_clip(
        self,
        reference_image: Path | str,
        cue: str,
        prompt: str,
        output_path: Path | str,
    ) -> Path:
        """Create a gesture clip from photo using ffmpeg (pan/zoom for subtle movement)."""
        out_file = Path(output_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        img_path = Path(reference_image)

        ffmpeg_exe = self.ffprobe._ffmpeg_path
        if not ffmpeg_exe or not img_path.exists():
            raise FileNotFoundError(f"Cần ffmpeg và ảnh tham chiếu: {img_path}")

        # Create a subtle zoom animation to make the still image feel alive
        cmd = [
            ffmpeg_exe,
            "-y",
            "-loop", "1",
            "-i", str(img_path),
            "-vf", (
                "scale=1080:1920:force_original_aspect_ratio=decrease,"
                "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,"
                # Subtle zoom 1.00 → 1.03 over 5 seconds for a "breathing" effect
                "zoompan=z='min(zoom+0.0006,1.03)':d=150:s=1080x1920:fps=30"
            ),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-t", "5.0",
            "-pix_fmt", "yuv420p",
            "-r", "30",
            # Silent audio track
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-c:a", "aac", "-b:a", "64k",
            "-shortest",
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
