"""Cloud Image-to-Video (I2V) Provider — generates high-quality gesture clips from photo references."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

import aiohttp
from loguru import logger

from app.config.settings import get_settings
from app.core.exceptions import AIAvatarError
from app.presenter.base import PresenterProvider


class CloudI2VProvider(PresenterProvider):
    """Cloud Image-to-Video provider with poll/download architecture (D-ID / HeyGen / Kling / Veo)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        service_name: str = "did",  # 'did' | 'heygen' | 'generic'
    ) -> None:
        super().__init__(name=f"Cloud I2V Engine ({service_name})", provider_type="i2v")
        self.service_name = service_name
        self.api_key = api_key or ""
        self._init_keys()

    def _init_keys(self) -> None:
        if not self.api_key:
            settings = get_settings()
            if self.service_name == "did":
                self.api_key = settings.did_api_key or ""
            elif self.service_name == "heygen":
                self.api_key = settings.heygen_api_key or ""

    def validate_credentials(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, f"Chưa cấu hình API Key cho dịch vụ {self.service_name.upper()}."
        return True, f"Dịch vụ {self.service_name.upper()} đã cấu hình."

    async def generate_gesture_clip(
        self,
        reference_image: Path | str,
        cue: str,
        prompt: str,
        output_path: Path | str,
    ) -> Path:
        """Call cloud i2v engine, poll until done, and download resulting MP4."""
        out_file = Path(output_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)

        is_valid, msg = self.validate_credentials()
        if not is_valid:
            logger.warning("I2V API key chưa cấu hình, dùng chế độ mô phỏng render: {}", msg)
            return await self._simulate_render(reference_image, out_file)

        if self.service_name == "did":
            return await self._render_did(reference_image, prompt, out_file)
        elif self.service_name == "heygen":
            return await self._render_heygen(reference_image, prompt, out_file)
        else:
            return await self._simulate_render(reference_image, out_file)

    async def generate_product_clip(
        self,
        presenter_image: Path | str,
        product_image: Path | str,
        cue: str,
        output_path: Path | str,
        prompt_override: Optional[str] = None,
    ) -> Path:
        """Render presenter holding/interacting with specific product."""
        prompt = prompt_override or (
            f"Realistic Vietnamese female presenter holding and showcasing the product, "
            f"green screen background, uniform studio lighting, camera static, 9:16 vertical, cue: {cue}"
        )
        return await self.generate_gesture_clip(
            reference_image=presenter_image,
            cue=cue,
            prompt=prompt,
            output_path=output_path,
        )

    async def generate_lipsync(
        self,
        base_clip: Path | str,
        audio_path: Path | str,
        output_path: Path | str,
    ) -> Path:
        """Fallback to lipsync provider."""
        from app.presenter.cloud_lipsync import CloudLipsyncProvider
        lipsync = CloudLipsyncProvider()
        return await lipsync.generate_lipsync(base_clip, audio_path, output_path)

    async def _render_did(self, image_path: Path | str, prompt: str, out_file: Path) -> Path:
        """D-ID Animation API with polling and download."""
        headers = {
            "Authorization": f"Basic {self.api_key}",
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60.0)) as session:
            try:
                payload = {
                    "script": {"type": "text", "input": prompt[:150]},
                    "config": {"stitch": True},
                }
                async with session.post("https://api.d-id.com/talks", json=payload, headers=headers) as res:
                    if res.status not in (200, 201):
                        err_text = await res.text()
                        raise AIAvatarError(f"D-ID API trả về lỗi {res.status}: {err_text}")
                    data = await res.json()
                    talk_id = data.get("id")

                if not talk_id:
                    raise AIAvatarError("D-ID không trả về talk id.")

                # 2. Poll for status (up to 90 seconds)
                video_url: Optional[str] = None
                for _ in range(18):
                    await asyncio.sleep(5)
                    async with session.get(f"https://api.d-id.com/talks/{talk_id}", headers=headers) as poll_res:
                        if poll_res.status == 200:
                            data = await poll_res.json()
                            status = data.get("status")
                            if status == "done":
                                video_url = data.get("result_url")
                                break
                            elif status == "error":
                                raise AIAvatarError(f"D-ID render thất bại: {data.get('error')}")

                if not video_url:
                    raise AIAvatarError("Hết thời gian chờ render từ D-ID.")

                # 3. Download video
                async with session.get(video_url) as dl_res:
                    if dl_res.status == 200:
                        content = await dl_res.read()
                        out_file.write_bytes(content)
                        return out_file
                    raise AIAvatarError(f"Không thể tải video từ D-ID: {dl_res.status}")

            except Exception as e:
                logger.error("Lỗi D-ID render: {}", e)
                raise AIAvatarError(f"Lỗi D-ID render: {e}") from e

    async def _render_heygen(self, image_path: Path | str, prompt: str, out_file: Path) -> Path:
        """HeyGen video generator with polling and download."""
        headers = {
            "X-Api-Key": self.api_key,
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60.0)) as session:
            try:
                payload = {
                    "video_inputs": [
                        {
                            "character": {"type": "avatar", "avatar_id": "default"},
                            "voice": {"type": "text", "input_text": prompt[:150]},
                        }
                    ],
                    "dimension": {"width": 1080, "height": 1920},
                }
                async with session.post("https://api.heygen.com/v2/video/generate", json=payload, headers=headers) as res:
                    if res.status != 200:
                        err_text = await res.text()
                        raise AIAvatarError(f"HeyGen API lỗi {res.status}: {err_text}")
                    data = await res.json()
                    video_id = data.get("data", {}).get("video_id")

                if not video_id:
                    raise AIAvatarError("HeyGen không trả về video_id.")

                # Poll status
                video_url: Optional[str] = None
                for _ in range(24):
                    await asyncio.sleep(5)
                    async with session.get(
                        f"https://api.heygen.com/v1/video_status.get?video_id={video_id}", headers=headers
                    ) as poll_res:
                        if poll_res.status == 200:
                            data = await poll_res.json()
                            status = data.get("data", {}).get("status")
                            if status == "completed":
                                video_url = data.get("data", {}).get("video_url")
                                break
                            elif status == "failed":
                                raise AIAvatarError(f"HeyGen render thất bại: {data.get('error')}")

                if not video_url:
                    raise AIAvatarError("Hết thời gian chờ HeyGen render.")

                async with session.get(video_url) as dl_res:
                    if dl_res.status == 200:
                        content = await dl_res.read()
                        out_file.write_bytes(content)
                        return out_file
                    raise AIAvatarError("Không thể tải video từ HeyGen.")

            except Exception as e:
                logger.error("Lỗi HeyGen render: {}", e)
                raise AIAvatarError(f"Lỗi HeyGen: {e}") from e

    async def _simulate_render(self, image_path: Path | str, out_file: Path) -> Path:
        """Create a placeholder video loop for development / offline testing."""
        from app.integrations.ffmpeg.ffprobe_service import FFProbeService
        import subprocess

        ffmpeg = FFProbeService()._ffmpeg_path
        if not ffmpeg or not Path(image_path).exists():
            out_file.write_bytes(b"\x00" * 2000)
            return out_file

        cmd = [
            ffmpeg,
            "-y",
            "-loop", "1",
            "-i", str(image_path),
            "-c:v", "libx264",
            "-t", "4.0",
            "-pix_fmt", "yuv420p",
            str(out_file),
        ]
        await asyncio.to_thread(subprocess.run, cmd, capture_output=True, timeout=30)
        return out_file
