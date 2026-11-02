"""End-to-End Orchestrator for Veo 3 AI Video Showcase Generation.

Coordinates Scene Scripting, Reference Frame Composition, Veo 3 Video Generation,
TTS Audio Alignment, and Video Stitching into a seamless automated pipeline.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Callable, Dict, List, Optional

from loguru import logger
from pydantic import BaseModel, Field

from app.config.settings import get_settings
from app.database.models.avatar import AIAvatar
from app.database.models.product import Product
from app.database.repositories.avatar_repo import AvatarRepository
from app.database.repositories.product_repo import ProductRepository
from app.integrations.tts.edge_tts_provider import EdgeTTSProvider
from app.integrations.veo.veo_client import VeoClient
from app.render.veo_preprocessor import VeoPreprocessor
from app.render.veo_stitcher import VeoStitcher
from app.services.veo_script_service import VeoScene, VeoScriptService, VeoShowcaseScript


class VeoProject(BaseModel):
    """An end-to-end video showcase project."""

    project_id: str
    product_id: int
    product_name: str
    avatar_id: Optional[int] = None
    avatar_name: str = "Người Mẫu AI"
    script: VeoShowcaseScript
    reference_frames: Dict[int, str] = Field(default_factory=dict)
    raw_video_clips: Dict[int, str] = Field(default_factory=dict)
    aligned_video_clips: Dict[int, str] = Field(default_factory=dict)
    status: str = "DRAFT"  # DRAFT, SCRIPT_READY, GENERATING, READY_TO_STITCH, COMPLETED, FAILED
    final_video_path: Optional[str] = None
    error_message: Optional[str] = None


class VeoShowcaseService:
    """High-level service coordinating the full Veo 3 generation pipeline."""

    def __init__(self, session=None) -> None:
        self.session = session
        self.script_service = VeoScriptService()
        self.veo_client = VeoClient()
        self.stitcher = VeoStitcher()
        self.tts = EdgeTTSProvider()
        self.settings = get_settings()

    async def create_showcase_project(
        self,
        product: Product,
        avatar: Optional[AIAvatar] = None,
        style: str = "energetic",
        voice_id: str = "vi-VN-HoaiMyNeural",
        speed: float = 1.0,
        num_scenes: int = 3,
        progress_callback: Optional[Callable[[str, int], None]] = None,
    ) -> VeoProject:
        """Create a new project, generate script, audio, and reference frames.

        Args:
            product: The product to advertise.
            avatar: Optional chosen AI model.
            style: Speaking/pitch style.
            voice_id: Edge TTS voice.
            speed: Speech speed rate.
            num_scenes: Number of scenes (typically 3: Hook, Demo, CTA).
            progress_callback: Progress update callback(message, percent).

        Returns:
            Initialized VeoProject ready for video generation.
        """
        import uuid

        project_id = f"veo_{uuid.uuid4().hex[:10]}"
        logger.info("Khởi tạo dự án Veo 3 [{}] cho sản phẩm: {}", project_id, product.name)

        if progress_callback:
            progress_callback("AI đang phân tích sản phẩm và viết kịch bản phân cảnh...", 15)

        # 1. Generate multi-scene script
        script = await self.script_service.generate_showcase_script(
            product=product,
            avatar=avatar,
            style=style,
            num_scenes=num_scenes,
        )

        project = VeoProject(
            project_id=project_id,
            product_id=product.id,
            product_name=product.name or "Sản phẩm",
            avatar_id=avatar.id if avatar else None,
            avatar_name=avatar.name if avatar else "Người Mẫu AI",
            script=script,
            status="SCRIPT_READY",
        )

        # 2. Synthesize TTS audio and create reference frames for each scene
        avatar_img = avatar.preview_image_path if avatar else None
        prod_img = product.image_path

        total_scenes = len(script.scenes)
        for idx, scene in enumerate(script.scenes):
            pct = 20 + int(30 * (idx + 1) / total_scenes)
            if progress_callback:
                progress_callback(
                    f"Đang chuẩn bị âm thanh & khung hình cho Phân cảnh {scene.scene_index} ({scene.shot_type})...",
                    pct,
                )

            # Synthesize TTS Audio
            audio_filename = f"{project_id}_scene_{scene.scene_index}_audio.mp3"
            audio_path = self.settings.effective_audio_dir / audio_filename
            try:
                rate_str = f"+{int((speed - 1.0) * 100)}%" if speed >= 1.0 else f"{int((speed - 1.0) * 100)}%"
                await self.tts.generate_speech(
                    text=scene.speech_text,
                    output_path=str(audio_path),
                    voice_id=voice_id,
                    rate=rate_str,
                )
                scene.audio_path = str(audio_path)
            except Exception as e:
                logger.warning("Lỗi tạo audio TTS cho scene {}: {}", scene.scene_index, e)

            # Create First Frame Composite
            frame_filename = f"{project_id}_scene_{scene.scene_index}_frame.jpg"
            frame_path = self.settings.effective_veo_dir / frame_filename
            try:
                comp_path = VeoPreprocessor.create_first_frame(
                    avatar_image_path=avatar_img,
                    product_image_path=prod_img,
                    shot_type=scene.shot_type,
                    output_path=frame_path,
                )
                project.reference_frames[scene.scene_index] = comp_path
            except Exception as e:
                logger.warning("Lỗi tạo khung hình tham chiếu cho scene {}: {}", scene.scene_index, e)

        if progress_callback:
            progress_callback("Kịch bản và khung hình đã sẵn sàng!", 50)

        return project

    async def generate_scene_video(
        self,
        project: VeoProject,
        scene_index: int,
        progress_callback: Optional[Callable[[str, int], None]] = None,
    ) -> str:
        """Generate or re-roll a specific scene video clip.

        Args:
            project: The showcase project.
            scene_index: 1-indexed scene number.
            progress_callback: Progress callback.

        Returns:
            Path to the aligned scene video clip.
        """
        target_scene = next((s for s in project.script.scenes if s.scene_index == scene_index), None)
        if not target_scene:
            raise ValueError(f"Không tìm thấy phân cảnh {scene_index} trong dự án.")

        ref_frame = project.reference_frames.get(scene_index)
        logger.info(
            "Bắt đầu render Veo 3 cho Scene {} [{}] - Prompt: {}",
            scene_index,
            target_scene.shot_type,
            target_scene.veo_prompt[:80],
        )

        target_scene.status = "GENERATING"

        # 1. Generate Raw Video from Gemini Omni / Veo
        raw_video_filename = f"{project.project_id}_scene_{scene_index}_raw.mp4"
        raw_video_path = self.settings.effective_veo_dir / raw_video_filename

        native_audio = bool(self.settings.veo_use_native_audio) and self.veo_client.model_name.startswith(
            self.veo_client.OMNI_PREFIX
        )
        prompt = target_scene.veo_prompt
        if native_audio and target_scene.speech_text:
            # The video model speaks the line itself, so the mouth matches the voice.
            prompt = (
                f"{prompt}\n\n"
                "Use the provided image as the first frame and keep the same person, outfit and product. "
                "The presenter looks at the camera and says the following line in Vietnamese, "
                "with a natural, energetic voice and matching lip movement: "
                f'"{target_scene.speech_text}". '
                f"Clip length about {target_scene.duration_seconds} seconds."
            )

        raw_clip = await self.veo_client.generate_video(
            prompt=prompt,
            image_path=ref_frame,
            aspect_ratio="9:16",
            duration_seconds=target_scene.duration_seconds,
            output_path=raw_video_path,
            progress_callback=progress_callback,
            audio_path=target_scene.audio_path,
            shot_type=target_scene.shot_type,
        )
        project.raw_video_clips[scene_index] = raw_clip

        # 2. Synchronize with TTS Audio & Subtitles
        if progress_callback:
            progress_callback(f"Đang đồng bộ âm thanh & phụ đề cho Phân cảnh {scene_index}...", 85)

        aligned_filename = f"{project.project_id}_scene_{scene_index}_aligned.mp4"
        aligned_path = self.settings.effective_veo_dir / aligned_filename

        if native_audio and self.stitcher.has_audio_stream(raw_clip):
            # Keep model's own speech (lip-synced); normalize resolution and burn in subtitles
            final_clip = await self.stitcher.align_scene_video_audio(
                video_path=raw_clip,
                audio_path=None,
                output_path=aligned_path,
                caption_text=target_scene.speech_text,
                use_video_audio=True,
            )
        elif target_scene.audio_path and Path(target_scene.audio_path).exists():
            final_clip = await self.stitcher.align_scene_video_audio(
                video_path=raw_clip,
                audio_path=target_scene.audio_path,
                output_path=aligned_path,
                caption_text=target_scene.speech_text,
            )
        else:
            final_clip = raw_clip

        project.aligned_video_clips[scene_index] = final_clip
        target_scene.video_path = final_clip
        target_scene.status = "COMPLETED"

        logger.info("Hoàn tất Phân cảnh {}: {}", scene_index, final_clip)
        return final_clip

    async def generate_all_scenes(
        self,
        project: VeoProject,
        progress_callback: Optional[Callable[[str, int], None]] = None,
    ) -> None:
        """Sequentially generate all scenes in the project."""
        project.status = "GENERATING"
        total_scenes = len(project.script.scenes)

        for i, scene in enumerate(project.script.scenes):
            scene_num = scene.scene_index

            def sub_callback(msg: str, pct: int):
                if progress_callback:
                    # Map scene progress into overall 0-90% range
                    base_pct = int(10 + (i / total_scenes) * 80)
                    scaled_pct = base_pct + int((pct / 100) * (80 / total_scenes))
                    progress_callback(f"[Cảnh {scene_num}/{total_scenes}] {msg}", min(92, scaled_pct))

            await self.generate_scene_video(project, scene_num, progress_callback=sub_callback)

        project.status = "READY_TO_STITCH"

    async def stitch_final_showcase_video(
        self,
        project: VeoProject,
        bgm_path: Optional[str | Path] = None,
        progress_callback: Optional[Callable[[str, int], None]] = None,
    ) -> str:
        """Stitch all aligned scenes into a complete showcase video and save to product.

        Args:
            project: Project with completed scenes.
            bgm_path: Optional background music path.
            progress_callback: Progress callback.

        Returns:
            Path to finished video MP4.
        """
        if progress_callback:
            progress_callback("Đang ghép nối toàn bộ các phân cảnh thành video bán hàng hoàn chỉnh...", 95)

        # Collect aligned clips in order
        ordered_clips = []
        for scene in project.script.scenes:
            clip = project.aligned_video_clips.get(scene.scene_index) or scene.video_path
            if clip and Path(clip).exists():
                ordered_clips.append(clip)

        if not ordered_clips:
            raise ValueError("Chưa có phân cảnh nào được tạo thành công để ghép nối.")

        out_filename = f"veo_showcase_{project.project_id}.mp4"
        out_path = self.settings.effective_media_dir / out_filename

        final_path = await self.stitcher.stitch_scenes(
            scene_paths=ordered_clips,
            output_path=out_path,
            bgm_path=bgm_path,
            bgm_volume=0.20,
        )

        project.final_video_path = final_path
        project.status = "COMPLETED"

        # Update product's video path if session available
        if project.product_id:
            try:
                repo = ProductRepository()
                repo.update(project.product_id, {"video_path": final_path})
                logger.info("Đã gắn video Veo hoàn thiện vào sản phẩm id={}", project.product_id)
            except Exception as e:
                logger.warning("Không thể lưu video_path vào database: {}", e)

        if progress_callback:
            progress_callback("Tạo video bán hàng Veo 3 hoàn tất 100%!", 100)

        logger.info("Xuất video thành công: {}", final_path)
        return final_path
