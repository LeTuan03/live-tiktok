"""Cloud Image-to-Video (I2V) Provider — generates high-quality gesture clips from photo references.

Fixed issues from code review:
- D-ID: Added source_url (upload image first, then reference in payload) — fixes lỗi #13
- HeyGen: Replaced hardcoded "default" avatar_id with configurable talking_photo — fixes lỗi #14
- Simulate render: Creates proper test video instead of garbage bytes
- Both providers: Full poll → download → verify pipeline
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

import aiohttp
from loguru import logger

from app.config.settings import get_settings
from app.core.exceptions import AIAvatarError
from app.presenter.base import PresenterProvider


# Default timeout for all cloud API operations (create + poll + download)
_CLOUD_TIMEOUT = aiohttp.ClientTimeout(total=180.0, connect=15.0)
_POLL_INTERVAL = 5  # seconds between status polls
_MAX_POLL_DID = 36  # 36 × 5s = 180s max wait for D-ID
_MAX_POLL_HEYGEN = 48  # 48 × 5s = 240s max wait for HeyGen


class CloudI2VProvider(PresenterProvider):
    """Cloud Image-to-Video provider with poll/download architecture (D-ID / HeyGen / Kling / Veo).

    Architecture: submit job → poll status → download MP4 → verify → return path.
    Falls back to simulate_render (ffmpeg placeholder) when API keys are missing.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        service_name: str = "did",  # 'did' | 'heygen' | 'generic'
        avatar_id: Optional[str] = None,  # HeyGen avatar ID or D-ID presenter ID
    ) -> None:
        super().__init__(name=f"Cloud I2V Engine ({service_name})", provider_type="i2v")
        self.service_name = service_name
        self.api_key = api_key or ""
        self.avatar_id = avatar_id  # User's specific avatar/presenter ID on the platform
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
        img_path = Path(reference_image)

        is_valid, msg = self.validate_credentials()
        if not is_valid:
            logger.warning("I2V API key chưa cấu hình, dùng chế độ mô phỏng render: {}", msg)
            return await self._simulate_render(img_path, out_file, cue=cue)

        if self.service_name == "did":
            return await self._render_did(img_path, prompt, out_file)
        elif self.service_name == "heygen":
            return await self._render_heygen(img_path, prompt, out_file)
        else:
            return await self._simulate_render(img_path, out_file, cue=cue)

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

    # ──────────────────────────────── D-ID ────────────────────────────────

    async def _upload_image_did(
        self, session: aiohttp.ClientSession, image_path: Path, headers: Dict[str, str]
    ) -> str:
        """Upload a local image to D-ID and return the public source_url.

        D-ID Talks API requires a publicly accessible URL for the source image.
        We use their /images endpoint to upload first.
        """
        upload_headers = {
            "Authorization": headers["Authorization"],
            "accept": "application/json",
        }

        if not image_path.exists():
            raise AIAvatarError(f"Ảnh người mẫu không tồn tại: {image_path}")

        # Read image and prepare multipart upload
        data = aiohttp.FormData()
        data.add_field(
            "image",
            image_path.read_bytes(),
            filename=image_path.name,
            content_type="image/png" if image_path.suffix.lower() == ".png" else "image/jpeg",
        )

        async with session.post(
            "https://api.d-id.com/images",
            data=data,
            headers=upload_headers,
        ) as res:
            if res.status not in (200, 201):
                err_text = await res.text()
                raise AIAvatarError(f"D-ID upload ảnh thất bại ({res.status}): {err_text}")
            result = await res.json()
            source_url = result.get("url")
            if not source_url:
                raise AIAvatarError(f"D-ID upload ảnh không trả về URL: {result}")
            logger.info("Đã upload ảnh người mẫu lên D-ID: {}", source_url[:80])
            return source_url

    async def _render_did(self, image_path: Path, prompt: str, out_file: Path) -> Path:
        """D-ID Talks API with proper source_url upload, polling and download.

        Fixed: Previously missing source_url in payload (lỗi #13).
        Now: Upload image first → get URL → create talk with source_url → poll → download.
        """
        headers = {
            "Authorization": f"Basic {self.api_key}",
            "Content-Type": "application/json",
            "accept": "application/json",
        }
        async with aiohttp.ClientSession(timeout=_CLOUD_TIMEOUT) as session:
            try:
                # 1. Upload presenter image to get a public URL
                source_url = await self._upload_image_did(session, image_path, headers)

                # 2. Create talk with source_url (THE FIX: this was missing before)
                payload = {
                    "source_url": source_url,
                    "script": {
                        "type": "text",
                        "input": prompt[:500],
                        "provider": {"type": "microsoft", "voice_id": "vi-VN-HoaiMyNeural"},
                    },
                    "config": {
                        "stitch": True,
                        "result_format": "mp4",
                    },
                }
                logger.info("Gửi yêu cầu render D-ID Talks với source_url...")
                async with session.post(
                    "https://api.d-id.com/talks", json=payload, headers=headers
                ) as res:
                    if res.status not in (200, 201):
                        err_text = await res.text()
                        raise AIAvatarError(f"D-ID API trả về lỗi {res.status}: {err_text}")
                    data = await res.json()
                    talk_id = data.get("id")

                if not talk_id:
                    raise AIAvatarError("D-ID không trả về talk id.")
                logger.info("D-ID talk đã tạo: {} — bắt đầu poll trạng thái...", talk_id)

                # 3. Poll for status
                video_url: Optional[str] = None
                for attempt in range(_MAX_POLL_DID):
                    await asyncio.sleep(_POLL_INTERVAL)
                    async with session.get(
                        f"https://api.d-id.com/talks/{talk_id}", headers=headers
                    ) as poll_res:
                        if poll_res.status == 200:
                            data = await poll_res.json()
                            status = data.get("status")
                            logger.debug("D-ID poll #{}: status={}", attempt + 1, status)
                            if status == "done":
                                video_url = data.get("result_url")
                                break
                            elif status in ("error", "rejected"):
                                err_detail = data.get("error", {})
                                raise AIAvatarError(
                                    f"D-ID render thất bại: {err_detail}"
                                )

                if not video_url:
                    raise AIAvatarError(
                        f"Hết thời gian chờ render từ D-ID ({_MAX_POLL_DID * _POLL_INTERVAL}s)."
                    )

                # 4. Download video
                logger.info("D-ID render hoàn tất, đang tải video...")
                async with session.get(video_url) as dl_res:
                    if dl_res.status == 200:
                        content = await dl_res.read()
                        if len(content) < 1000:
                            raise AIAvatarError(
                                f"D-ID trả về file quá nhỏ ({len(content)} bytes) — có thể lỗi."
                            )
                        out_file.write_bytes(content)
                        logger.info("Đã tải video D-ID: {} ({:.1f} KB)", out_file.name, len(content) / 1024)
                        return out_file
                    raise AIAvatarError(f"Không thể tải video từ D-ID: HTTP {dl_res.status}")

            except AIAvatarError:
                raise
            except Exception as e:
                logger.error("Lỗi D-ID render: {}", e)
                raise AIAvatarError(f"Lỗi D-ID render: {e}") from e

    # ──────────────────────────────── HeyGen ────────────────────────────────

    async def _render_heygen(self, image_path: Path, prompt: str, out_file: Path) -> Path:
        """HeyGen video generator with proper avatar handling, polling and download.

        Fixed: Previously used hardcoded avatar_id:"default" which is invalid (lỗi #14).
        Now: Uses talking_photo type with uploaded image, or user-configured avatar_id.
        """
        headers = {
            "X-Api-Key": self.api_key,
            "Content-Type": "application/json",
            "accept": "application/json",
        }
        async with aiohttp.ClientSession(timeout=_CLOUD_TIMEOUT) as session:
            try:
                # Determine character config based on available info
                if self.avatar_id:
                    # User has a specific HeyGen avatar_id configured
                    character_config = {
                        "type": "avatar",
                        "avatar_id": self.avatar_id,
                    }
                    logger.info("Sử dụng HeyGen avatar ID: {}", self.avatar_id)
                else:
                    # Use talking_photo: upload the presenter image
                    # First upload to get a talking_photo_id
                    talking_photo_url = await self._upload_image_heygen(
                        session, image_path, headers
                    )
                    character_config = {
                        "type": "talking_photo",
                        "talking_photo_url": talking_photo_url,
                    }
                    logger.info("Sử dụng HeyGen talking_photo từ ảnh upload")

                payload = {
                    "video_inputs": [
                        {
                            "character": character_config,
                            "voice": {
                                "type": "text",
                                "input_text": prompt[:500],
                                "voice_id": "vi_female_mai",  # Vietnamese female voice
                            },
                        }
                    ],
                    "dimension": {"width": 1080, "height": 1920},
                }

                logger.info("Gửi yêu cầu render HeyGen...")
                async with session.post(
                    "https://api.heygen.com/v2/video/generate",
                    json=payload,
                    headers=headers,
                ) as res:
                    if res.status not in (200, 201):
                        err_text = await res.text()
                        raise AIAvatarError(f"HeyGen API lỗi {res.status}: {err_text}")
                    data = await res.json()
                    video_id = data.get("data", {}).get("video_id")

                if not video_id:
                    raise AIAvatarError(f"HeyGen không trả về video_id. Response: {data}")
                logger.info("HeyGen video đã tạo: {} — bắt đầu poll...", video_id)

                # Poll status
                video_url: Optional[str] = None
                for attempt in range(_MAX_POLL_HEYGEN):
                    await asyncio.sleep(_POLL_INTERVAL)
                    async with session.get(
                        f"https://api.heygen.com/v1/video_status.get?video_id={video_id}",
                        headers=headers,
                    ) as poll_res:
                        if poll_res.status == 200:
                            data = await poll_res.json()
                            status = data.get("data", {}).get("status")
                            logger.debug("HeyGen poll #{}: status={}", attempt + 1, status)
                            if status == "completed":
                                video_url = data.get("data", {}).get("video_url")
                                break
                            elif status in ("failed", "error"):
                                err_detail = data.get("data", {}).get("error", data)
                                raise AIAvatarError(
                                    f"HeyGen render thất bại: {err_detail}"
                                )

                if not video_url:
                    raise AIAvatarError(
                        f"Hết thời gian chờ HeyGen render ({_MAX_POLL_HEYGEN * _POLL_INTERVAL}s)."
                    )

                # Download video
                logger.info("HeyGen render hoàn tất, đang tải video...")
                async with session.get(video_url) as dl_res:
                    if dl_res.status == 200:
                        content = await dl_res.read()
                        if len(content) < 1000:
                            raise AIAvatarError(
                                f"HeyGen trả về file quá nhỏ ({len(content)} bytes)."
                            )
                        out_file.write_bytes(content)
                        logger.info("Đã tải video HeyGen: {} ({:.1f} KB)", out_file.name, len(content) / 1024)
                        return out_file
                    raise AIAvatarError(f"Không thể tải video từ HeyGen: HTTP {dl_res.status}")

            except AIAvatarError:
                raise
            except Exception as e:
                logger.error("Lỗi HeyGen render: {}", e)
                raise AIAvatarError(f"Lỗi HeyGen: {e}") from e

    async def _upload_image_heygen(
        self, session: aiohttp.ClientSession, image_path: Path, headers: Dict[str, str]
    ) -> str:
        """Upload presenter image to HeyGen for talking_photo generation."""
        if not image_path.exists():
            raise AIAvatarError(f"Ảnh người mẫu không tồn tại: {image_path}")

        upload_headers = {
            "X-Api-Key": headers["X-Api-Key"],
            "accept": "application/json",
        }

        data = aiohttp.FormData()
        data.add_field(
            "file",
            image_path.read_bytes(),
            filename=image_path.name,
            content_type="image/png" if image_path.suffix.lower() == ".png" else "image/jpeg",
        )

        async with session.post(
            "https://api.heygen.com/v1/talking_photo",
            data=data,
            headers=upload_headers,
        ) as res:
            if res.status not in (200, 201):
                err_text = await res.text()
                raise AIAvatarError(f"HeyGen upload ảnh thất bại ({res.status}): {err_text}")
            result = await res.json()
            photo_url = result.get("data", {}).get("talking_photo_url") or result.get("data", {}).get("url")
            if not photo_url:
                # Fallback: use the talking_photo_id
                photo_id = result.get("data", {}).get("talking_photo_id")
                if photo_id:
                    return photo_id
                raise AIAvatarError(f"HeyGen upload ảnh không trả về URL: {result}")
            return photo_url

    # ──────────────────────────────── Simulate ────────────────────────────────

    async def _simulate_render(
        self, image_path: Path, out_file: Path, cue: str = "IDLE_TALK"
    ) -> Path:
        """Create a proper test video for development / offline testing.

        Fixed: Previously wrote 2KB of null bytes which is not a valid video.
        Now: Creates an actual MP4 with the reference image + text overlay.
        """
        from app.integrations.ffmpeg.ffprobe_service import FFProbeService

        ffmpeg = FFProbeService()._ffmpeg_path
        if not ffmpeg:
            logger.warning("ffmpeg không khả dụng — tạo video placeholder tối thiểu")
            # Create the tiniest valid-looking file (still not playable, but better than zeros)
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_bytes(b"")
            return out_file

        # Use reference image if it exists, otherwise generate a solid color frame
        if image_path.exists():
            input_args = ["-loop", "1", "-i", str(image_path)]
            vf_filter = (
                f"scale=1080:1920:force_original_aspect_ratio=decrease,"
                f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,"
                f"drawtext=text='[SIM] {cue}':fontsize=36:fontcolor=yellow:"
                f"x=(w-text_w)/2:y=h-100:box=1:boxcolor=black@0.6:boxborderw=8"
            )
        else:
            input_args = [
                "-f", "lavfi",
                "-i", f"color=c=0x1A1A2E:s=1080x1920:d=5:r=30",
            ]
            vf_filter = (
                f"drawtext=text='[SIMULATE] {cue}':fontsize=48:fontcolor=white:"
                f"x=(w-text_w)/2:y=(h-text_h)/2:box=1:boxcolor=black@0.5:boxborderw=12"
            )

        cmd = [
            ffmpeg,
            "-y",
            *input_args,
            "-vf", vf_filter,
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-t", "5.0",
            "-pix_fmt", "yuv420p",
            "-r", "30",
            # Add silent audio track so the file plays properly in OBS
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-c:a", "aac", "-b:a", "64k",
            "-shortest",
            str(out_file),
        ]

        try:
            result = await asyncio.to_thread(
                subprocess.run, cmd, capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                logger.warning("ffmpeg simulate render stderr: {}", result.stderr[-300:] if result.stderr else "")
        except Exception as e:
            logger.warning("Lỗi simulate render: {}", e)

        if out_file.exists() and out_file.stat().st_size > 1000:
            logger.info("Đã tạo video mô phỏng: {} (cue: {})", out_file.name, cue)
        else:
            logger.warning("Video mô phỏng có thể bị lỗi: {} ({} bytes)", out_file.name,
                           out_file.stat().st_size if out_file.exists() else 0)

        return out_file
