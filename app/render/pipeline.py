"""Render Pipeline — the END-TO-END orchestrator that takes a product + script → final MP4.

This is the MISSING LINK that connects all the v2 modules together:
    Product data + Script segments
        → TTS per segment (with ffprobe for REAL duration)
        → Presenter clip selection/generation (gesture + lipsync)
        → Shot planning (camera cuts every 8-15s)
        → Banner generation (price/name overlay)
        → Compositor (7-layer ffmpeg render)
        → Verification (ffprobe quality check)
        → Ready-to-play MP4

Usage:
    pipeline = RenderPipeline()
    result = await pipeline.render_product_video(
        product_name="Kem dưỡng XYZ",
        product_price=350000,
        promo_price=190000,
        product_image="path/to/product.png",
        script_text="Chào cả nhà...",  # or StructuredScript
        avatar_id=1,
        set_id="quay_my_pham_trang",
    )
    # result.output_path → "storage/rendered/product_1_v1.mp4"
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from app.config.settings import get_settings
from app.integrations.ffmpeg.ffprobe_service import FFProbeService
from app.presenter import create_provider, PresenterClipLibrary
from app.render.banner import BannerGenerator
from app.render.compositor import VideoCompositor
from app.render.shot_planner import ShotPlanner
from app.services.script_segment_service import (
    StructuredScript,
    parse_and_validate_script_json,
)
from app.sets.library import SceneSetLibrary
from app.sets.scene_set import SceneSet


@dataclass
class RenderResult:
    """Result of a full render pipeline execution."""

    success: bool = False
    output_path: Optional[str] = None
    duration_seconds: float = 0.0
    shot_count: int = 0
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    steps_completed: List[str] = field(default_factory=list)
    render_time_seconds: float = 0.0
    verification: Optional[Dict[str, Any]] = None


class RenderPipeline:
    """End-to-end video rendering pipeline: Product → MP4.

    Orchestrates all v2 modules in sequence:
    1. Parse/validate script into segments
    2. TTS each segment → get REAL duration from ffprobe
    3. Select/generate presenter clips per segment cue
    4. Plan shot timeline (camera cuts)
    5. Generate banner overlay
    6. Run compositor (7-layer ffmpeg)
    7. Verify output
    """

    def __init__(
        self,
        presenter_service_name: str = "lipsync",
        presenter_api_key: str = "",
        presenter_avatar_id: str = "",
    ) -> None:
        self.settings = get_settings()
        self.ffprobe = FFProbeService()
        self.scene_library = SceneSetLibrary()
        self.shot_planner = ShotPlanner()
        self.compositor = VideoCompositor()
        self.banner_gen = BannerGenerator()
        self.clip_library = PresenterClipLibrary()
        self.presenter_provider = create_provider(
            service_name=presenter_service_name,
            api_key=presenter_api_key,
            avatar_id=presenter_avatar_id,
        )

        # Storage directories
        self.render_dir = self.settings.data_dir / "rendered"
        self.render_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir = self.settings.data_dir / "render_temp"
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    async def render_product_video(
        self,
        product_name: str,
        product_price: Optional[float] = None,
        promo_price: Optional[float] = None,
        product_image: Optional[str | Path] = None,
        closeup_media: Optional[str | Path] = None,
        script_text: Optional[str] = None,
        structured_script: Optional[StructuredScript] = None,
        avatar_id: Optional[int] = None,
        set_id: str = "studio_gradient",
        output_path: Optional[str | Path] = None,
        product_id: Optional[int] = None,
        on_progress: Optional[callable] = None,
    ) -> RenderResult:
        """Execute the full render pipeline for one product.

        Args:
            product_name: Display name of the product.
            product_price: Original price in VND.
            promo_price: Promotional/live price in VND.
            product_image: Path to main product photo.
            closeup_media: Path to closeup detail photo/video.
            script_text: Raw script text (will be parsed into segments).
            structured_script: Pre-parsed StructuredScript (takes priority over script_text).
            avatar_id: ID of the presenter avatar in the clip library.
            set_id: ID of the scene set (background environment).
            output_path: Where to save the final MP4.
            product_id: Database product ID (for naming).
            on_progress: Callback(step_name, percent, message).

        Returns:
            RenderResult with output path, duration, and diagnostics.
        """
        start_time = time.time()
        result = RenderResult()

        def _progress(step: str, pct: int, msg: str) -> None:
            result.steps_completed.append(f"[{pct}%] {step}: {msg}")
            if on_progress:
                try:
                    on_progress(step, pct, msg)
                except Exception:
                    pass
            logger.info("[Render Pipeline {}%] {}: {}", pct, step, msg)

        try:
            # ─── Step 1: Parse script into segments ───
            _progress("script", 5, "Phân tích kịch bản phân đoạn...")

            if structured_script:
                script = structured_script
            elif script_text:
                script = parse_and_validate_script_json(script_text, default_set_id=set_id)
            else:
                result.errors.append("Không có kịch bản (script_text hoặc structured_script).")
                return result

            if not script.segments:
                result.errors.append("Kịch bản không có đoạn nào sau khi parse.")
                return result

            _progress("script", 10, f"Đã parse {len(script.segments)} đoạn kịch bản")

            # ─── Step 2: TTS each segment → get REAL duration ───
            _progress("tts", 15, "Tổng hợp giọng nói cho từng đoạn...")
            from app.services.voice_service import VoiceService
            from app.database.repositories.voice_repo import VoiceRepository
            from app.database.session import get_session

            voice_svc = VoiceService(VoiceRepository(get_session()))

            segment_audios: List[Path] = []
            total_audio_duration = 0.0

            for i, seg in enumerate(script.segments):
                tts_text = seg.clean_text_for_tts()
                if not tts_text.strip():
                    result.warnings.append(f"Đoạn {i+1} không có text sau khi lọc stage directions")
                    continue

                audio_file = self.temp_dir / f"seg_{product_id or 0}_{i:02d}.mp3"
                try:
                    audio_path = voice_svc.synthesize_speech(
                        text=tts_text,
                        output_path=audio_file,
                    )
                    # Get REAL duration from ffprobe (not estimate)
                    probe = self.ffprobe.probe(audio_path)
                    real_dur = probe.duration if probe and probe.duration else 10.0
                    seg.duration_seconds = real_dur
                    seg.audio_path = str(audio_path)
                    total_audio_duration += real_dur
                    segment_audios.append(Path(audio_path))
                except Exception as e:
                    result.warnings.append(f"TTS đoạn {i+1} lỗi: {e}")
                    seg.duration_seconds = 10.0

                pct = 15 + int((i / len(script.segments)) * 15)
                _progress("tts", pct, f"TTS {i+1}/{len(script.segments)}: {real_dur:.1f}s")

            if not segment_audios:
                result.errors.append("Không tạo được audio TTS nào.")
                return result

            _progress("tts", 30, f"Tổng thời lượng audio: {total_audio_duration:.1f}s")

            # ─── Step 3: Concatenate all segment audios into master track ───
            _progress("audio_concat", 32, "Ghép audio master track...")
            master_audio = self.temp_dir / f"master_{product_id or 0}.mp3"
            master_audio = self._concat_audios(segment_audios, master_audio)

            # ─── Step 4: Select/generate presenter clip ───
            _progress("presenter", 35, "Chuẩn bị clip người mẫu AI...")
            presenter_clip_path: Optional[Path] = None

            if avatar_id is not None:
                # Try to find an approved clip from the library
                first_cue = script.segments[0].cue if script.segments else "IDLE_TALK"
                clip = self.clip_library.get_clip_for_cue(avatar_id, first_cue, approved_only=True)
                if not clip:
                    clip = self.clip_library.get_clip_for_cue(avatar_id, "IDLE_TALK", approved_only=False)

                if clip and Path(clip.file_path).exists():
                    presenter_clip_path = Path(clip.file_path)
                    _progress("presenter", 40, f"Sử dụng clip cử chỉ: {presenter_clip_path.name}")

                    # Generate lipsync version
                    lipsync_output = self.temp_dir / f"lipsync_{product_id or 0}.mp4"
                    try:
                        presenter_clip_path = await self.presenter_provider.generate_lipsync(
                            base_clip=presenter_clip_path,
                            audio_path=master_audio,
                            output_path=lipsync_output,
                        )
                        _progress("presenter", 50, "Đã đồng bộ lipsync người mẫu")
                    except Exception as e:
                        result.warnings.append(f"Lipsync thất bại, dùng clip gốc: {e}")
                else:
                    result.warnings.append(
                        f"Không tìm thấy clip cử chỉ đã duyệt cho avatar {avatar_id}. "
                        "Video sẽ không có người mẫu AI."
                    )
            else:
                result.warnings.append("Không chọn người mẫu AI. Video sẽ chỉ có bối cảnh + sản phẩm.")

            _progress("presenter", 50, "Hoàn tất chuẩn bị người mẫu")

            # ─── Step 5: Plan shot timeline ───
            _progress("shots", 55, "Lên lịch cắt cảnh...")
            has_closeup = bool(closeup_media and Path(closeup_media).exists())
            shot_plan = self.shot_planner.plan_shots(
                structured_script=script,
                has_closeup_asset=has_closeup,
            )
            result.shot_count = shot_plan.shot_switches_count
            _progress("shots", 60, f"Đã lên {shot_plan.shot_count} shot, {result.shot_count} lần đổi góc máy")

            if not shot_plan.is_valid_review_rhythm() and total_audio_duration >= 30:
                result.warnings.append(
                    f"Nhịp cắt cảnh chưa đạt chuẩn review (chỉ {result.shot_count} lần đổi góc, cần ≥3)"
                )

            # ─── Step 6: Generate banner overlay ───
            _progress("banner", 65, "Tạo banner giá/tên sản phẩm...")
            banner_path = self.temp_dir / f"banner_{product_id or 0}.png"
            try:
                self.banner_gen.generate_product_banner(
                    output_path=str(banner_path),
                    product_name=product_name,
                    original_price=product_price,
                    live_price=promo_price,
                )
                _progress("banner", 70, "Đã tạo banner 9:16")
            except Exception as e:
                result.warnings.append(f"Tạo banner lỗi: {e}")
                banner_path = None

            # ─── Step 7: Get scene set ───
            scene_set = self.scene_library.get_set_or_default(set_id)
            _progress("scene", 72, f"Bối cảnh: {scene_set.name}")

            # ─── Step 8: Run compositor ───
            if output_path is None:
                version = int(time.time()) % 10000
                output_path = self.render_dir / f"product_{product_id or 0}_v{version}.mp4"
            output_path = Path(output_path)

            _progress("render", 75, "Bắt đầu render video 1080×1920 (ffmpeg compositor)...")

            rendered = self.compositor.render_composition(
                output_mp4=output_path,
                scene_set=scene_set,
                audio_path=str(master_audio),
                product_image=str(product_image) if product_image else None,
                closeup_media=str(closeup_media) if closeup_media else None,
                banner_path=str(banner_path) if banner_path else None,
                presenter_clip=str(presenter_clip_path) if presenter_clip_path else None,
                shot_plan=shot_plan,
                duration_seconds=total_audio_duration,
            )

            _progress("render", 90, "Render hoàn tất, đang kiểm tra chất lượng...")

            # ─── Step 9: Verify output ───
            verification = self.compositor.verify_rendered_video(
                rendered, expected_duration=total_audio_duration
            )
            result.verification = verification

            if not verification.get("valid", True):
                v_errors = verification.get("errors", [])
                result.warnings.extend(v_errors)
                _progress("verify", 95, f"Cảnh báo kiểm tra: {'; '.join(v_errors)}")
            else:
                _progress("verify", 95, "Video đạt chuẩn kiểm tra ✅")

            # ─── Done ───
            result.success = True
            result.output_path = str(rendered)
            result.duration_seconds = total_audio_duration
            result.render_time_seconds = time.time() - start_time

            _progress("done", 100,
                       f"✅ Hoàn tất! {result.duration_seconds:.0f}s video, "
                       f"{result.shot_count} lần đổi cảnh, "
                       f"render trong {result.render_time_seconds:.1f}s")

        except Exception as e:
            logger.exception("Lỗi nghiêm trọng trong render pipeline")
            result.errors.append(f"Lỗi pipeline: {e}")
            result.render_time_seconds = time.time() - start_time

        return result

    def _concat_audios(self, audio_files: List[Path], output: Path) -> Path:
        """Concatenate multiple audio files into a single master audio track using ffmpeg."""
        if len(audio_files) == 1:
            # Just copy/use the single file
            import shutil
            shutil.copy2(str(audio_files[0]), str(output))
            return output

        ffmpeg_exe = self.ffprobe._ffmpeg_path
        if not ffmpeg_exe:
            raise RuntimeError("ffmpeg không khả dụng để ghép audio")

        import subprocess
        import tempfile

        # Create concat list file
        concat_list = output.parent / f"{output.stem}_concat.txt"
        with open(concat_list, "w", encoding="utf-8") as f:
            for af in audio_files:
                # ffmpeg concat requires forward slashes and escaped single quotes
                safe_path = str(af.resolve()).replace("\\", "/")
                f.write(f"file '{safe_path}'\n")

        cmd = [
            ffmpeg_exe,
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list),
            "-c:a", "libmp3lame",
            "-b:a", "192k",
            str(output),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            logger.warning("ffmpeg concat audio lỗi: {}", result.stderr[-300:] if result.stderr else "")

        # Clean up concat list
        try:
            concat_list.unlink(missing_ok=True)
        except Exception:
            pass

        if output.exists() and output.stat().st_size > 0:
            return output

        # Fallback: just use the first file
        import shutil
        shutil.copy2(str(audio_files[0]), str(output))
        return output
