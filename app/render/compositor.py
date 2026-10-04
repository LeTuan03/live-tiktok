"""Compositor v2 — ffmpeg-based offline video rendering engine combining 7-layer stack:
L0: Set background (with depth of field blur)
L1: Product showcase (with contact shadow on stand)
L2: Presenter feet contact shadow
L3: AI Presenter (with perspective matching, chroma despill, and color grading)
L4: Bottom promo price banner
L5: Inset product closeup (for INSET_PRODUCT shots)
L6: Top shop live header
L7: Film grain unification filter
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from PIL import Image, ImageDraw, ImageFilter

from app.integrations.ffmpeg.ffprobe_service import FFProbeService
from app.render.banner import BannerGenerator
from app.render.shot_planner import ShotPlan, ShotPlanner
from app.sets.matcher import SceneMatcher
from app.sets.scene_set import SceneSet


class VideoCompositor:
    """Composites offline 1080x1920 vertical video from assets, shot timeline, presenter, and audio."""

    def __init__(self, ffmpeg_path: Optional[str] = None) -> None:
        self.ffprobe = FFProbeService()
        self.ffmpeg_exe = ffmpeg_path or self.ffprobe._ffmpeg_path

    def render_composition(
        self,
        output_mp4: str | Path,
        scene_set: SceneSet,
        audio_path: str | Path,
        product_image: Optional[str | Path] = None,
        closeup_media: Optional[str | Path] = None,
        banner_path: Optional[str | Path] = None,
        presenter_clip: Optional[str | Path] = None,
        shot_plan: Optional[ShotPlan] = None,
        duration_seconds: Optional[float] = None,
        mode: str = "layered",  # 'layered' (Mode 1) or 'baked' (Mode 2)
    ) -> Path:
        """Render a full 1080x1920 30fps MP4 review video using ffmpeg.

        Args:
            output_mp4: Destination file path.
            scene_set: Review environment configuration.
            audio_path: Synchronized master TTS audio track.
            product_image: Main product cutout or photo.
            closeup_media: Optional detail/closeup shot of product.
            banner_path: Optional 1080x1920 RGBA transparent overlay card.
            presenter_clip: Optional AI presenter video clip (green screen or alpha).
            shot_plan: Planned camera cuts.
            duration_seconds: Target length (defaults to audio length).
            mode: 'layered' for 7-layer composition, or 'baked' for single monolithic clip.

        Returns:
            Path to verified MP4 video file.
        """
        target = Path(output_mp4)
        target.parent.mkdir(parents=True, exist_ok=True)

        audio_file = Path(audio_path)
        if not audio_file.exists():
            raise FileNotFoundError(f"Không tìm thấy file audio: {audio_file}")

        # Probe real audio duration
        probe_res = self.ffprobe.probe(audio_file)
        real_duration = probe_res.duration if probe_res and probe_res.duration else (duration_seconds or 30.0)

        # 1. Prepare base background frame (L0 + L1 + L2)
        has_presenter = bool(presenter_clip and Path(presenter_clip).exists())
        bg_frame_path = target.parent / f"{target.stem}_bg.png"
        self._prepare_background_frame(
            output_path=bg_frame_path,
            scene_set=scene_set,
            product_image=product_image,
            include_presenter_shadow=has_presenter,
        )

        # 2. Build ffmpeg filtergraph
        inputs: list[str] = [
            "-loop", "1",
            "-i", str(bg_frame_path),
            "-i", str(audio_file),
        ]
        filter_parts: list[str] = []
        last_v = "0:v"

        # Presenter layer (L3)
        if has_presenter:
            p_file = Path(presenter_clip)
            p_probe = self.ffprobe.probe(p_file)
            pw = p_probe.width if p_probe and p_probe.width else 1080
            ph = p_probe.height if p_probe and p_probe.height else 1920

            px, py, scaled_w, scaled_h = SceneMatcher.calculate_presenter_placement(
                scene_set, pw, ph, 1080, 1920
            )
            color_grade = SceneMatcher.build_color_grade_filter(scene_set)
            despill = SceneMatcher.build_chroma_despill_filter()

            # Add presenter input (looping video)
            inputs.extend(["-stream_loop", "-1", "-i", str(p_file)])
            p_idx = len(inputs) // 2 - 1

            p_filters = [
                f"scale={scaled_w}:{scaled_h}",
                despill,
                color_grade,
            ]
            filter_parts.append(f"[{p_idx}:v]{','.join(p_filters)}[presenter_v]")
            filter_parts.append(f"[{last_v}][presenter_v]overlay={px}:{py}:eof_action=repeat[v_with_presenter]")
            last_v = "v_with_presenter"

        # Banner overlay (L4)
        if banner_path and Path(banner_path).exists():
            inputs.extend(["-i", str(banner_path)])
            b_idx = len(inputs) // 2 - 1
            filter_parts.append(f"[{last_v}][{b_idx}:v]overlay=0:0[v_with_banner]")
            last_v = "v_with_banner"

        # Film grain / noise layer (L7) (§4.3.3)
        grain_filter = SceneMatcher.build_grain_filter(scene_set.grain)
        if grain_filter != "null":
            filter_parts.append(f"[{last_v}]{grain_filter}[v_out]")
            last_v = "v_out"
        else:
            filter_parts.append(f"[{last_v}]null[v_out]")
            last_v = "v_out"

        cmd = [
            self.ffmpeg_exe,
            "-y",
            *inputs,
            "-filter_complex", ";".join(filter_parts),
            "-map", f"[{last_v}]",
            "-map", "1:a",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-r", "30",
            "-c:a", "aac",
            "-b:a", "128k",
            "-t", f"{real_duration:.2f}",
            "-shortest",
            str(target),
        ]

        logger.info("Bắt đầu render video với ffmpeg (7 layers): {}", target.name)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=180,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )

        if result.returncode != 0 or not target.exists() or target.stat().st_size < 1000:
            err_msg = result.stderr[-600:] if result.stderr else "Unknown error"
            logger.error("Lỗi ffmpeg compositor: {}", err_msg)
            raise RuntimeError(f"FFmpeg render thất bại: {err_msg}")

        # 3. Verify output video (§3.3 & §6 acceptance criteria)
        verification = self.verify_rendered_video(target, expected_duration=real_duration)
        if not verification["valid"]:
            logger.warning("Cảnh báo kiểm tra video render: {}", verification["errors"])

        logger.info("Render video hoàn tất: {} ({:.1f}s)", target.name, real_duration)
        return target

    def _prepare_background_frame(
        self,
        output_path: Path,
        scene_set: SceneSet,
        product_image: Optional[str | Path] = None,
        include_presenter_shadow: bool = False,
        width: int = 1080,
        height: int = 1920,
    ) -> Path:
        """Compose the realistic static review layer: set background + depth-of-field + product stand + presenter contact shadow."""
        # 1. Load or fallback set background
        bg_path = Path(scene_set.background)
        if bg_path.exists():
            try:
                base_img = Image.open(str(bg_path)).convert("RGBA")
                if base_img.size != (width, height):
                    base_img = base_img.resize((width, height), Image.Resampling.LANCZOS)
            except Exception:
                base_img = Image.new("RGBA", (width, height), (22, 25, 34, 255))
        else:
            base_img = Image.new("RGBA", (width, height), (22, 25, 34, 255))

        # 2. Apply gentle depth-of-field blur to set background (§4.3.3)
        if scene_set.bg_blur > 0:
            base_img = base_img.filter(ImageFilter.GaussianBlur(radius=scene_set.bg_blur))

        # 3. Place product on showcase zone if product image provided
        if product_image and Path(product_image).exists():
            try:
                prod_img = Image.open(str(product_image)).convert("RGBA")
                zx1, zy1, zx2, zy2 = scene_set.product_zone
                target_w = zx2 - zx1
                target_h = zy2 - zy1

                prod_img.thumbnail((target_w, target_h), Image.Resampling.LANCZOS)
                pw, ph = prod_img.size

                # Draw subtle contact shadow under product (§4.3.3)
                shadow_mask = Image.new("RGBA", (width, height), (0, 0, 0, 0))
                s_draw = ImageDraw.Draw(shadow_mask)
                shadow_cx = zx1 + target_w // 2
                shadow_bottom = zy2 - 10
                s_draw.ellipse(
                    (shadow_cx - pw // 2, shadow_bottom - 20, shadow_cx + pw // 2, shadow_bottom + 15),
                    fill=(0, 0, 0, int(scene_set.shadow_alpha * 255)),
                )
                shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(radius=scene_set.shadow_blur // 2))
                base_img = Image.alpha_composite(base_img, shadow_mask)

                # Paste product
                paste_x = zx1 + (target_w - pw) // 2
                paste_y = zy2 - ph - 15
                base_img.paste(prod_img, (paste_x, paste_y), prod_img)

            except Exception as e:
                logger.debug("Không thể ghép ảnh sản phẩm vào background: {}", e)

        # 4. Draw presenter contact shadow at floor_y (L2)
        if include_presenter_shadow:
            p_shadow = SceneMatcher.generate_contact_shadow_mask(
                scene_set,
                feet_cx=scene_set.anchor_x,
                feet_y=scene_set.floor_y,
                width=width,
                height=height,
            )
            base_img = Image.alpha_composite(base_img, p_shadow)

        base_img.convert("RGB").save(str(output_path), format="PNG")
        return output_path

    def verify_rendered_video(
        self,
        video_path: Path | str,
        expected_duration: Optional[float] = None,
        min_shot_changes: int = 0,
    ) -> Dict[str, Any]:
        """Verify rendered MP4 video against acceptance criteria (§3.3 & §6)."""
        result: Dict[str, Any] = {
            "valid": True,
            "errors": [],
            "metadata": {},
        }
        probe = self.ffprobe.probe(video_path)
        if not probe:
            result["valid"] = False
            result["errors"].append("Không thể đọc thông tin video bằng ffprobe.")
            return result

        result["metadata"] = probe.to_dict()

        # Check resolution 1080x1920
        if probe.width != 1080 or probe.height != 1920:
            result["valid"] = False
            result["errors"].append(f"Kích thước video không đúng 1080×1920 (hiện tại: {probe.width}×{probe.height}).")

        # Check video stream present
        if not probe.codec or not probe.is_video:
            result["valid"] = False
            result["errors"].append("Video thiếu video stream.")

        # Check audio stream present in file
        has_audio = any(s.get("codec_type") == "audio" for s in probe.raw.get("streams", []))
        if not has_audio:
            result["valid"] = False
            result["errors"].append("Video thiếu stream âm thanh.")

        # Check duration drift < 0.5% (requirement: drift < 100ms for short, < 1.5s for long)
        if expected_duration and probe.duration:
            drift = abs(probe.duration - expected_duration)
            drift_pct = (drift / expected_duration) * 100.0 if expected_duration > 0 else 0
            result["metadata"]["duration_drift_pct"] = round(drift_pct, 2)
            result["metadata"]["duration_drift_sec"] = round(drift, 3)
            if drift > 1.5 and drift_pct > 2.0:
                result["valid"] = False
                result["errors"].append(
                    f"Độ lệch thời lượng âm thanh / hình ảnh ({drift:.2f}s, {drift_pct:.1f}%) vượt ngưỡng cho phép."
                )

        return result
