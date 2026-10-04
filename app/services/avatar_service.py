"""Avatar Service for managing AI Virtual Presenters, OBS Overlays, and Lip-sync Video Generation."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from loguru import logger

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
        """Create a placeholder indicator image — NOT a usable avatar.

        Bug #2 fix: previous version drew geometric shapes that looked terrible
        on stream. This now creates a clearly-labeled placeholder that won't
        be mistaken for a real presenter.
        """
        filename = f"avatar_{initial.lower()}_{abs(hash(name)) % 10000}.png"
        out_path = self.avatar_dir / filename
        if out_path.exists():
            return str(out_path)

        try:
            from PIL import Image, ImageDraw

            width, height = 400, 600
            # Gray background — NOT green screen, to make it obvious this is a placeholder
            img = Image.new("RGBA", (width, height), (60, 60, 60, 255))
            draw = ImageDraw.Draw(img)

            # Hex to RGB helper
            h = bg_hex.lstrip("#")
            if len(h) == 6:
                bg_rgb = tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))
            else:
                bg_rgb = (108, 92, 231)

            # Draw a camera icon placeholder instead of fake person
            draw.rounded_rectangle([40, 60, 360, 540], radius=24, fill=(80, 80, 80))
            draw.text((200, 200), "📷", anchor="mm")
            draw.text((200, 300), "PLACEHOLDER", fill=(200, 200, 200), anchor="mm")
            short_name = name.split("—")[0].strip()
            draw.text((200, 340), f"{short_name}", fill=(150, 150, 150), anchor="mm")
            draw.text((200, 420), "Thêm clip thật", fill=bg_rgb, anchor="mm")
            draw.text((200, 450), "để sử dụng", fill=bg_rgb, anchor="mm")

            img.save(str(out_path))
            return str(out_path)
        except Exception as e:
            logger.warning("Không thể tạo avatar art: {}", e)
            return ""


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

        hash_key = self.voice_service.compute_audio_hash(clean_text, voice_id, speed)[:12]
        out_name = f"pitch_{hash_key}.mp3"
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
        """D-ID Talks API integration — DISABLED pending rewrite.

        Bug #13: source_url was computed but never included in payload.
        Bug #13b: D-ID requires a public URL, not a local file path.
        This provider will be rewritten in P3 with proper API handling.
        """
        return None, (
            "D-ID tạm thời bị vô hiệu hoá. "
            "Lý do: API integration chưa hoàn chỉnh (thiếu source_url trong payload, "
            "không hỗ trợ file local). Sẽ được viết lại trong giai đoạn P3."
        )

    async def _generate_heygen_video(
        self,
        api_key: str,
        avatar: AIAvatar,
        script_text: str,
    ) -> Tuple[Optional[str], str]:
        """HeyGen video generation — DISABLED pending rewrite.

        Bug #14: original code submitted the job but never polled for completion
        or downloaded the result. Also used invalid avatar_id 'default'.
        This provider will be rewritten in P3 with proper polling and download.
        """
        return None, (
            "HeyGen tạm thời bị vô hiệu hoá. "
            "Lý do: API integration chưa hoàn chỉnh (không poll kết quả, không tải video về, "
            "avatar_id không hợp lệ). Sẽ được viết lại trong giai đoạn P3."
        )

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
