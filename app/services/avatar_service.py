"""Avatar Service for managing AI Virtual Presenters, OBS Overlays, and Lip-sync Video Generation."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from loguru import logger

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPen
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.database.models.avatar import AIAvatar
from app.database.models.product import Product
from app.database.repositories.avatar_repo import AvatarRepository
from app.database.session import get_session
from app.integrations.obs.client import OBSClient
from app.services.settings_service import (
    KEY_DID_API_KEY,
    KEY_HEYGEN_API_KEY,
    KEY_OBS_AVATAR_SOURCE,
    KEY_OBS_SCENE,
    SettingsService,
)
from app.database.repositories.voice_repo import VoiceRepository
from app.services.voice_service import VoiceService


class AvatarService:
    """Service layer for AI digital presenters and livestream models."""

    def __init__(self, session: Optional[Session] = None) -> None:
        self.session = session or get_session()
        self.repo = AvatarRepository(self.session)
        self.settings_service = SettingsService(self.session)
        self.voice_service = VoiceService(VoiceRepository(self.session))
        self.settings = get_settings()
        self.avatar_dir = self.settings.effective_avatar_dir
        self.avatar_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_preset_avatars()

    def _ensure_preset_avatars(self) -> None:
        """Seed high-quality preset avatars if repository is empty."""
        try:
            if self.repo.count() > 0:
                return

            presets = [
                {
                    "name": "Mai Phương — MC Thời Trang & Mỹ Phẩm",
                    "gender": "female",
                    "style": "Thanh lịch, Duyên dáng",
                    "avatar_type": "video_loop",
                    "voice_id": "vi-VN-HoaiMyNeural",
                    "description": "Nữ MC thanh lịch, biểu cảm tự nhiên, phát âm chuẩn truyền cảm. Thích hợp cho ngành hàng thời trang, mỹ phẩm, mẹ & bé.",
                    "is_default": True,
                    "transparent_bg": True,
                    "chroma_key_color": "#00FF00",
                    "obs_position": "bottom_right",
                    "obs_scale": 0.38,
                    "avatar_color": "#FD79A8",
                    "initial": "MP",
                },
                {
                    "name": "Thu Thảo — Nữ Streamer Chốt Đơn",
                    "gender": "female",
                    "style": "Năng động, Sôi nổi",
                    "avatar_type": "video_loop",
                    "voice_id": "vi-VN-HoaiMyNeural",
                    "description": "Phong cách trẻ trung, nhiệt huyết, đẩy mood cực tốt, kích thích người xem tương tác và chốt deal chớp nhoáng.",
                    "is_default": False,
                    "transparent_bg": True,
                    "chroma_key_color": "#00FF00",
                    "obs_position": "bottom_right",
                    "obs_scale": 0.38,
                    "avatar_color": "#6C5CE7",
                    "initial": "TT",
                },
                {
                    "name": "Minh Khôi — Reviewer Công Nghệ & Gia Dụng",
                    "gender": "male",
                    "style": "Chuyên gia, Đáng tin cậy",
                    "avatar_type": "video_loop",
                    "voice_id": "vi-VN-NamMinhNeural",
                    "description": "Nam chuyên gia am hiểu kỹ thuật, giải thích tính năng rõ ràng, tạo niềm tin cao cho sản phẩm công nghệ và gia dụng.",
                    "is_default": False,
                    "transparent_bg": True,
                    "chroma_key_color": "#00FF00",
                    "obs_position": "bottom_right",
                    "obs_scale": 0.38,
                    "avatar_color": "#0984E3",
                    "initial": "MK",
                },
                {
                    "name": "Hải Đăng — Nam MC Lịch Lãm",
                    "gender": "male",
                    "style": "Điềm đạm, Tinh tế",
                    "avatar_type": "video_loop",
                    "voice_id": "vi-VN-NamMinhNeural",
                    "description": "Giọng nam ấm áp, phong thái tự tin, sang trọng. Phù hợp cho đồ gia dụng cao cấp, phụ kiện nam và quà tặng.",
                    "is_default": False,
                    "transparent_bg": True,
                    "chroma_key_color": "#00FF00",
                    "obs_position": "bottom_left",
                    "obs_scale": 0.38,
                    "avatar_color": "#00B894",
                    "initial": "HĐ",
                },
            ]

            for p in presets:
                thumb_path = self._generate_avatar_art(p["name"], p["avatar_color"], p["initial"])
                self.repo.create(
                    name=p["name"],
                    gender=p["gender"],
                    style=p["style"],
                    avatar_type=p["avatar_type"],
                    media_path=thumb_path,
                    preview_image_path=thumb_path,
                    transparent_bg=p["transparent_bg"],
                    chroma_key_color=p["chroma_key_color"],
                    voice_id=p["voice_id"],
                    description=p["description"],
                    is_default=p["is_default"],
                    obs_position=p["obs_position"],
                    obs_scale=p["obs_scale"],
                )
            logger.info("Đã khởi tạo 4 Người Mẫu AI mẫu trong hệ thống.")
        except Exception as e:
            logger.exception("Lỗi khi khởi tạo Người Mẫu AI mẫu: {}", e)

    def _generate_avatar_art(self, name: str, bg_hex: str, initial: str) -> str:
        """Create a premium 2D avatar portrait image if no asset file is provided."""
        filename = f"avatar_{initial.lower()}_{abs(hash(name)) % 10000}.png"
        out_path = self.avatar_dir / filename
        if out_path.exists():
            return str(out_path)

        # Ensure QGuiApplication exists for QPainter font handling
        from PySide6.QtGui import QGuiApplication
        import sys
        if QGuiApplication.instance() is None:
            _ = QGuiApplication(sys.argv)

        width, height = 400, 600
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        # Transparent green chroma background (or modern gradient for preview)
        image.fill(QColor(0, 255, 0, 255))  # Green screen canvas for chroma key testing

        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Draw a stylish character silhouette / avatar badge
        card_rect = QRect(40, 60, 320, 480)
        painter.setBrush(QBrush(QColor(bg_hex)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(card_rect, 24, 24)

        # Head / Hair
        painter.setBrush(QBrush(QColor(255, 255, 255, 230)))
        painter.drawEllipse(130, 110, 140, 140)

        # Body / Shoulders
        painter.setBrush(QBrush(QColor(255, 255, 255, 180)))
        painter.drawChord(QRect(80, 230, 240, 240), 0, 180 * 16)

        # Initials badge
        painter.setFont(QFont("Segoe UI", 36, QFont.Weight.Bold))
        painter.setPen(QColor(bg_hex))
        painter.drawText(QRect(130, 110, 140, 140), Qt.AlignmentFlag.AlignCenter, initial)

        # Name label at bottom
        painter.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        painter.setPen(QColor(255, 255, 255))
        short_name = name.split("—")[0].strip()
        painter.drawText(QRect(40, 480, 320, 40), Qt.AlignmentFlag.AlignCenter, f"AI {short_name}")

        painter.end()
        image.save(str(out_path))
        return str(out_path)

    # ------------------------------------------------------------ CRUD
    def get_avatars(self, active_only: bool = True) -> Sequence[AIAvatar]:
        return self.repo.get_all(active_only=active_only)

    def get_avatar(self, avatar_id: int) -> Optional[AIAvatar]:
        return self.repo.get_by_id(avatar_id)

    def get_default_avatar(self) -> Optional[AIAvatar]:
        return self.repo.get_default()

    def create_avatar(self, data: dict) -> AIAvatar:
        return self.repo.create(**data)

    def update_avatar(self, avatar_id: int, data: dict) -> Optional[AIAvatar]:
        return self.repo.update(avatar_id, **data)

    def delete_avatar(self, avatar_id: int) -> bool:
        return self.repo.delete(avatar_id)

    # ------------------------------------------------------------ OBS Operations
    def send_avatar_to_obs(
        self,
        obs_client: OBSClient,
        avatar: AIAvatar,
        scene_name: str = "",
    ) -> Tuple[bool, str]:
        """Activate the AI Avatar in the current OBS livestream scene."""
        if not obs_client.is_connected:
            return False, "Chưa kết nối tới OBS Studio. Vui lòng kết nối OBS trước."

        scene = scene_name or self.settings_service.get_obs_scene()
        avatar_source = self.settings_service.get(KEY_OBS_AVATAR_SOURCE) or "LiveFlow Avatar"

        # Ensure source exists in scene
        obs_client.ensure_liveflow_setup(
            scene_name=scene,
            video_source=self.settings_service.get_obs_video_source(),
            audio_source=self.settings_service.get_obs_audio_source(),
            avatar_source=avatar_source,
        )

        media_file = avatar.media_path or avatar.preview_image_path or ""
        if not media_file or not os.path.exists(media_file):
            return False, f"Tệp media của Người Mẫu AI '{avatar.name}' không tồn tại."

        ok = obs_client.set_avatar_media_file(
            input_name=avatar_source,
            file_path=media_file,
            loop=True,
            chroma_key=avatar.transparent_bg,
            scene_name=scene,
            position=avatar.obs_position or "bottom_right",
            scale=avatar.obs_scale or 0.38,
        )
        if ok:
            obs_client.set_scene_item_visible(scene, avatar_source, True)
            return True, f"Đã đưa Người Mẫu AI '{avatar.name}' lên OBS scene '{scene}' thành công!"
        return False, f"Không thể cập nhật Người Mẫu AI trên OBS."

    def hide_avatar_in_obs(self, obs_client: OBSClient, scene_name: str = "") -> bool:
        """Hide the AI Avatar overlay in OBS."""
        if not obs_client.is_connected:
            return False
        scene = scene_name or self.settings_service.get_obs_scene()
        avatar_source = self.settings_service.get(KEY_OBS_AVATAR_SOURCE) or "LiveFlow Avatar"
        return obs_client.set_scene_item_visible(scene, avatar_source, False)

    # ------------------------------------------------------------ Audio & Video Synthesis
    async def generate_speech_for_pitch(
        self,
        text: str,
        voice_id: str = "vi-VN-HoaiMyNeural",
        speed: float = 1.0,
    ) -> Tuple[Optional[str], float]:
        """Generate TTS audio for the product pitch and return (audio_path, duration_seconds)."""
        clean_text = text.strip()
        if not clean_text:
            return None, 0.0

        out_name = f"pitch_{abs(hash(clean_text)) % 1000000}_{int(speed * 100)}.mp3"
        out_path = self.settings.effective_audio_dir / out_name

        try:
            # Check cache
            if out_path.exists() and out_path.stat().st_size > 100:
                duration = self.voice_service.get_audio_duration(str(out_path))
                return str(out_path), duration

            # Synthesize using VoiceService
            audio_path = await self.voice_service.synthesize_speech(
                text=clean_text,
                voice_id=voice_id,
                speed=speed,
                output_path=str(out_path),
            )
            duration = self.voice_service.get_audio_duration(audio_path)
            return audio_path, duration
        except Exception as e:
            logger.exception("Lỗi khi sinh giọng đọc giới thiệu sản phẩm: {}", e)
            return None, 0.0

    async def generate_lip_sync_video(
        self,
        avatar: AIAvatar,
        audio_path: str,
        script_text: str,
        output_name: str = "",
    ) -> Tuple[Optional[str], str]:
        """Generate a complete lip-sync/talking presenter video.

        1. If D-ID API key is configured -> Calls D-ID Talks API.
        2. If HeyGen API key is configured -> Calls HeyGen Video Generate API.
        3. Fallback Smart Engine -> Uses FFmpeg to composite the avatar with audio
           into a synchronized presenter video ready for livestream or media library.
        """
        did_key = self.settings_service.get_secret(KEY_DID_API_KEY)
        heygen_key = self.settings_service.get_secret(KEY_HEYGEN_API_KEY)

        if did_key:
            return await self._generate_did_video(did_key, avatar, audio_path, script_text)
        elif heygen_key:
            return await self._generate_heygen_video(heygen_key, avatar, script_text)
        else:
            return await self._generate_composite_video(avatar, audio_path, output_name)

    async def _generate_did_video(
        self,
        api_key: str,
        avatar: AIAvatar,
        audio_path: str,
        script_text: str,
    ) -> Tuple[Optional[str], str]:
        """D-ID Talks API integration."""
        try:
            import httpx

            url = "https://api.d-id.com/talks"
            headers = {
                "Authorization": f"Basic {api_key}",
                "Content-Type": "application/json",
            }
            # Use avatar image
            source_url = avatar.preview_image_path or avatar.media_path or ""
            payload = {
                "script": {
                    "type": "text",
                    "input": script_text,
                    "provider": {
                        "type": "microsoft",
                        "voice_id": avatar.voice_id or "vi-VN-HoaiMyNeural",
                    },
                },
                "config": {"fluent": True, "pad_audio": 0.0},
            }

            async with httpx.AsyncClient(timeout=60.0) as client:
                res = await client.post(url, json=payload, headers=headers)
                if res.status_code not in (200, 201):
                    return None, f"D-ID API Error ({res.status_code}): {res.text}"
                data = res.json()
                talk_id = data.get("id")

                # Poll for completion
                for _ in range(30):
                    await asyncio.sleep(2.0)
                    poll = await client.get(f"{url}/{talk_id}", headers=headers)
                    if poll.status_code == 200:
                        p_data = poll.json()
                        status = p_data.get("status")
                        if status == "done":
                            result_url = p_data.get("result_url")
                            # Download to media directory
                            dest = self.settings.effective_media_dir / f"did_avatar_{talk_id}.mp4"
                            vid_res = await client.get(result_url)
                            dest.write_bytes(vid_res.content)
                            return str(dest), "Tạo video D-ID thành công!"
                        elif status == "error":
                            return None, f"D-ID render failed: {p_data.get('error')}"

                return None, "D-ID render timed out sau 60s."
        except Exception as e:
            logger.exception("D-ID generation error: {}", e)
            return None, f"Lỗi gọi D-ID API: {e}"

    async def _generate_heygen_video(
        self,
        api_key: str,
        avatar: AIAvatar,
        script_text: str,
    ) -> Tuple[Optional[str], str]:
        """HeyGen video generation integration."""
        try:
            import httpx

            url = "https://api.heygen.com/v2/video/generate"
            headers = {
                "X-Api-Key": api_key,
                "Content-Type": "application/json",
            }
            payload = {
                "video_inputs": [
                    {
                        "character": {
                            "type": "avatar",
                            "avatar_id": "default",
                            "avatar_style": "normal",
                        },
                        "voice": {
                            "type": "text",
                            "input_text": script_text,
                            "voice_id": avatar.voice_id or "vi-VN-HoaiMyNeural",
                        },
                    }
                ],
                "dimension": {"width": 1080, "height": 1920},
            }

            async with httpx.AsyncClient(timeout=60.0) as client:
                res = await client.post(url, json=payload, headers=headers)
                if res.status_code not in (200, 201):
                    return None, f"HeyGen API Error ({res.status_code}): {res.text}"
                data = res.json()
                video_id = data.get("data", {}).get("video_id")
                return None, f"HeyGen video submitted (ID: {video_id})."
        except Exception as e:
            return None, f"Lỗi gọi HeyGen API: {e}"

    async def _generate_composite_video(
        self,
        avatar: AIAvatar,
        audio_path: str,
        output_name: str = "",
    ) -> Tuple[Optional[str], str]:
        """Smart Engine: Combines avatar visual loop with TTS audio into a presenter video clip."""
        if not audio_path or not os.path.exists(audio_path):
            return None, "File âm thanh TTS không tồn tại."

        source_media = avatar.media_path or avatar.preview_image_path
        if not source_media or not os.path.exists(source_media):
            return None, "Không tìm thấy file hình ảnh hoặc video của Người Mẫu AI."

        safe_name = output_name or f"avatar_pitch_{avatar.id}_{os.path.basename(audio_path).split('.')[0]}.mp4"
        out_video_path = self.settings.effective_media_dir / safe_name

        ffmpeg_cmd = self.settings.ffmpeg_path or "ffmpeg"
        try:
            # Probe audio duration
            duration = self.voice_service.get_audio_duration(audio_path)
            if duration <= 0:
                duration = 10.0

            # If input is image, loop image over audio duration
            ext = os.path.splitext(source_media)[1].lower()
            if ext in (".png", ".jpg", ".jpeg", ".webp"):
                cmd = [
                    ffmpeg_cmd,
                    "-y",
                    "-loop", "1",
                    "-i", str(source_media),
                    "-i", str(audio_path),
                    "-c:v", "libx264",
                    "-tune", "stillimage",
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-pix_fmt", "yuv420p",
                    "-t", f"{duration:.2f}",
                    "-shortest",
                    str(out_video_path),
                ]
            else:
                # Video loop combined with audio
                cmd = [
                    ffmpeg_cmd,
                    "-y",
                    "-stream_loop", "-1",
                    "-i", str(source_media),
                    "-i", str(audio_path),
                    "-map", "0:v:0",
                    "-map", "1:a:0",
                    "-c:v", "libx264",
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-t", f"{duration:.2f}",
                    "-shortest",
                    str(out_video_path),
                ]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0 and out_video_path.exists():
                return str(out_video_path), f"Đã tạo video Người Mẫu AI giới thiệu ({duration:.1f}s) thành công!"
            else:
                err_msg = stderr.decode(errors="replace")[-300:]
                logger.warning("FFmpeg render avatar video failed: {}", err_msg)
                return None, f"Lỗi render video: {err_msg}"
        except Exception as e:
            logger.exception("Error in composite video generation: {}", e)
            return None, f"Lỗi tạo video: {e}"
