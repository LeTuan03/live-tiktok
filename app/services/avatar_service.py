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

    # Mapping of preset avatar initials to their bundled image filenames.
    # These are real AI-generated presenter images with green screen backgrounds,
    # shipped inside storage/avatars/ in the project directory.
    # Mapping of preset avatar initials to their bundled filenames (video loop, still image).
    # Shipped inside storage/avatars/ in the project directory.
    _BUNDLED_AVATAR_FILES = {
        "MP": ("mai_phuong.mp4", "mai_phuong.jpg"),
        "TT": ("thu_thao.mp4", "thu_thao.jpg"),
        "MK": ("minh_khoi.mp4", "minh_khoi.jpg"),
        "HĐ": ("hai_dang.mp4", "hai_dang.jpg"),
    }

    def _get_bundled_avatars_dir(self) -> Path:
        """Return the path to the bundled avatar images and videos shipped with the project."""
        from app.config.settings import get_project_root
        return get_project_root() / "storage" / "avatars"

    def _resolve_avatar_media(self, initial: str, name: str) -> tuple[str, str]:
        """Copy the bundled AI-generated avatar media (video and photo) to user data dir.

        Returns (media_path, preview_image_path).
        """
        bundled_files = self._BUNDLED_AVATAR_FILES.get(initial)
        video_dest = ""
        image_dest = ""

        if bundled_files:
            video_name, image_name = bundled_files
            bundled_dir = self._get_bundled_avatars_dir()

            # Copy image
            src_img = bundled_dir / image_name
            if src_img.exists():
                dst_img = self.avatar_dir / image_name
                if not dst_img.exists():
                    shutil.copy2(str(src_img), str(dst_img))
                    logger.info("Đã copy ảnh người mẫu AI: {} → {}", src_img.name, dst_img)
                image_dest = str(dst_img)

            # Copy video loop
            src_vid = bundled_dir / video_name
            if src_vid.exists():
                dst_vid = self.avatar_dir / video_name
                if not dst_vid.exists():
                    shutil.copy2(str(src_vid), str(dst_vid))
                    logger.info("Đã copy video loop người mẫu AI: {} → {}", src_vid.name, dst_vid)
                video_dest = str(dst_vid)

        # Fallbacks
        if not image_dest:
            image_dest = self._generate_avatar_placeholder(name, initial)
        if not video_dest:
            video_dest = image_dest

        return video_dest, image_dest

    def _resolve_avatar_image(self, initial: str, name: str) -> str:
        """Backward-compatible helper returning the preview image path."""
        _, img_path = self._resolve_avatar_media(initial, name)
        return img_path

    def _ensure_preset_avatars(self) -> None:
        """Seed high-quality preset avatars with real AI-generated images if repository is empty.

        Also upgrades existing avatars that still point to old gray placeholder images
        to use the new bundled AI-generated presenter images.
        """
        try:
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
                    "initial": "HĐ",
                },
            ]

            if self.repo.count() > 0:
                # Upgrade existing avatars: replace old placeholders with real images
                self._upgrade_existing_avatars_images(presets)
                return

            # First-time seed: create all 4 preset avatars with real videos and images
            for p in presets:
                vid_path, img_path = self._resolve_avatar_media(p["initial"], p["name"])
                self.repo.create(
                    name=p["name"],
                    gender=p["gender"],
                    style=p["style"],
                    avatar_type=p["avatar_type"],
                    media_path=vid_path,
                    preview_image_path=img_path,
                    transparent_bg=p["transparent_bg"],
                    chroma_key_color=p["chroma_key_color"],
                    voice_id=p["voice_id"],
                    description=p["description"],
                    is_default=p["is_default"],
                    obs_position=p["obs_position"],
                    obs_scale=p["obs_scale"],
                )
            logger.info("Đã khởi tạo 4 Người Mẫu AI với video loop và ảnh AI thực tế (green screen) trong hệ thống.")
        except Exception as e:
            logger.exception("Lỗi khi khởi tạo Người Mẫu AI mẫu: {}", e)

    def _upgrade_existing_avatars_images(self, presets: list) -> None:
        """Upgrade existing preset avatars to use real bundled MP4 video loops and JPG photos."""
        existing_avatars = self.repo.get_all(active_only=False)
        preset_map = {}
        for p in presets:
            short_name = p["name"].split("—")[0].strip()
            preset_map[short_name] = p

        for avatar in existing_avatars:
            short_name = avatar.name.split("—")[0].strip()
            preset = preset_map.get(short_name)
            if not preset:
                # Custom avatar or missing media
                if not avatar.media_path or not os.path.exists(avatar.media_path):
                    def_vid, def_img = self._resolve_avatar_media("MP", "Mai Phương")
                    self.repo.update(avatar.id, media_path=def_vid, preview_image_path=def_img)
                continue

            needs_upgrade = False
            current_media = avatar.media_path or ""

            if not current_media or not os.path.exists(current_media):
                needs_upgrade = True
            elif current_media.endswith(".png"):
                needs_upgrade = True
            elif current_media.endswith(".jpg"):
                # Upgrade from static JPG to MP4 video loop
                needs_upgrade = True

            if needs_upgrade:
                vid_path, img_path = self._resolve_avatar_media(preset["initial"], preset["name"])
                if vid_path:
                    self.repo.update(
                        avatar.id,
                        media_path=vid_path,
                        preview_image_path=img_path,
                    )
                    logger.info(
                        "Đã nâng cấp người mẫu AI '{}' → video loop '{}'",
                        avatar.name,
                        os.path.basename(vid_path),
                    )

    def _generate_avatar_placeholder(self, name: str, initial: str) -> str:
        """Create a styled placeholder image as last resort when bundled images are unavailable.

        This generates a professional-looking silhouette on green screen background,
        suitable for chroma key, rather than the old ugly gray placeholder.
        """
        filename = f"avatar_{initial.lower()}_{abs(hash(name)) % 10000}.png"
        out_path = self.avatar_dir / filename
        if out_path.exists():
            return str(out_path)

        try:
            from PIL import Image, ImageDraw

            width, height = 720, 1080
            # Green screen background — ready for chroma key
            img = Image.new("RGB", (width, height), (0, 200, 0))
            draw = ImageDraw.Draw(img)

            cx, cy = width // 2, 200

            # Head
            draw.ellipse([cx - 55, cy - 55, cx + 55, cy + 55], fill=(70, 70, 80))

            # Neck
            draw.rectangle([cx - 18, cy + 50, cx + 18, cy + 90], fill=(70, 70, 80))

            # Body
            draw.polygon(
                [(cx - 100, cy + 90), (cx - 120, cy + 350),
                 (cx + 120, cy + 350), (cx + 100, cy + 90)],
                fill=(50, 50, 60),
            )

            # Arms
            draw.polygon(
                [(cx - 100, cy + 90), (cx - 130, cy + 300),
                 (cx - 90, cy + 300), (cx - 65, cy + 120)],
                fill=(50, 50, 60),
            )
            draw.polygon(
                [(cx + 100, cy + 90), (cx + 130, cy + 300),
                 (cx + 90, cy + 300), (cx + 65, cy + 120)],
                fill=(50, 50, 60),
            )

            # Hands
            draw.ellipse([cx - 135, cy + 280, cx - 85, cy + 330], fill=(70, 70, 80))
            draw.ellipse([cx + 85, cy + 280, cx + 135, cy + 330], fill=(70, 70, 80))

            # Legs
            draw.polygon(
                [(cx - 100, cy + 340), (cx - 90, cy + 650),
                 (cx - 30, cy + 650), (cx - 10, cy + 340)],
                fill=(40, 40, 45),
            )
            draw.polygon(
                [(cx + 10, cy + 340), (cx + 30, cy + 650),
                 (cx + 90, cy + 650), (cx + 100, cy + 340)],
                fill=(40, 40, 45),
            )

            # Shoes
            draw.ellipse([cx - 95, cy + 640, cx - 25, cy + 680], fill=(30, 30, 30))
            draw.ellipse([cx + 25, cy + 640, cx + 95, cy + 680], fill=(30, 30, 30))

            # Face features
            draw.ellipse([cx - 25, cy - 15, cx - 10, cy], fill=(255, 255, 255))
            draw.ellipse([cx + 10, cy - 15, cx + 25, cy], fill=(255, 255, 255))
            draw.arc([cx - 20, cy + 10, cx + 20, cy + 35], 0, 180, fill=(255, 255, 255), width=2)

            img.save(str(out_path), quality=95)
            logger.info("Đã tạo ảnh placeholder green screen cho '{}': {}", name, out_path)
            return str(out_path)
        except Exception as e:
            logger.warning("Không thể tạo avatar placeholder: {}", e)
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
            # Locate base image for facial animation
            source_image = avatar.preview_image_path or avatar.media_path or ""
            if source_image.lower().endswith(".mp4"):
                jpg_candidate = Path(source_image).with_suffix(".jpg")
                if jpg_candidate.exists():
                    source_image = str(jpg_candidate)

            if not os.path.exists(source_image):
                return None, "Không tìm thấy file hình ảnh gốc của Người Mẫu AI để tạo cử động nói."

            # Use AvatarAnimator to generate realistic speech lip-sync, nodding, and blinking
            from app.render.avatar_animator import AvatarAnimator
            animator = AvatarAnimator(ffmpeg_bin=ffmpeg_cmd)

            await asyncio.to_thread(
                animator.generate_talking_video,
                image_path=source_image,
                audio_path=audio_path,
                output_mp4=out_video_path,
                fps=25,
            )

            if out_video_path.exists():
                duration = self.voice_service.get_audio_duration(audio_path)
                return str(out_video_path), f"Đã tạo video Người Mẫu AI cử động nói và giới thiệu ({duration:.1f}s) thành công!"
            else:
                return None, "Không tìm thấy tệp video sau khi xuất."

        except Exception as e:
            logger.exception("Error in AvatarAnimator talking video generation: {}", e)
            return None, f"Lỗi tạo video cử động nói: {e}"
