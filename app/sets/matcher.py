"""Scene Matcher — enforces the 5 anti-collage realism rules (§4.3.3) for presenter & set integration."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from loguru import logger
from PIL import Image, ImageDraw, ImageFilter

from app.sets.scene_set import SceneSet


class SceneMatcher:
    """Calculates scaling, positioning, color temperature grading, contact shadow, and despill filtergraphs."""

    @staticmethod
    def calculate_presenter_placement(
        scene_set: SceneSet,
        presenter_w: int,
        presenter_h: int,
        target_canvas_w: int = 1080,
        target_canvas_h: int = 1920,
    ) -> Tuple[int, int, int, int]:
        """Rule 1: Khớp phối cảnh.
        
        Calculates (x, y, scaled_w, scaled_h) so that the presenter's feet touch floor_y exactly
        and their height in the frame matches model_height_px.
        """
        target_h = scene_set.model_height_px
        scale = target_h / float(presenter_h) if presenter_h > 0 else 1.0
        scaled_w = int(round(presenter_w * scale))
        scaled_h = target_h

        # Center horizontally at anchor_x
        x = int(round(scene_set.anchor_x - (scaled_w / 2.0)))
        # Feet exactly at floor_y
        y = int(round(scene_set.floor_y - scaled_h))

        return x, y, scaled_w, scaled_h

    @staticmethod
    def build_color_grade_filter(scene_set: SceneSet) -> str:
        """Rule 2: Khớp màu và ánh sáng theo light_temp (§4.3.3)."""
        temp = scene_set.light_temp.upper()
        # Parse approximate Kelvin or preset
        if any(k in temp for k in ("3000K", "3500K", "4000K", "ẤM", "WARM")):
            # Warm color temperature: boost red/yellow, decrease blue
            return "colorbalance=rs=0.08:gs=0.02:bs=-0.08,eq=saturation=1.05"
        elif any(k in temp for k in ("6500K", "7000K", "LẠNH", "COOL", "NEON")):
            # Cool color temperature: boost blue, decrease red
            return "colorbalance=rs=-0.06:gs=0.0:bs=0.09,eq=saturation=1.1"
        else:  # 5600K neutral daylight
            return "eq=saturation=1.02:contrast=1.02"

    @staticmethod
    def build_chroma_despill_filter(chroma_hex: str = "#00FF00") -> str:
        """Clean green screen matte with despill to eliminate edge green fringe (§4.3.3)."""
        # ffmpeg colorkey or chromakey filter with despill
        clean_hex = chroma_hex.lstrip("#")
        return f"colorkey=0x{clean_hex}:0.28:0.10,despill=type=green:mix=0.5"

    @staticmethod
    def generate_contact_shadow_mask(
        scene_set: SceneSet,
        feet_cx: int,
        feet_y: int,
        width: int = 1080,
        height: int = 1920,
    ) -> Image.Image:
        """Rule 3: Bóng đổ tiếp xúc (L2) - blurred contact shadow ellipse at floor_y."""
        mask = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(mask)

        # Contact shadow under presenter's feet
        rx = 160
        ry = 32
        alpha = int(round(scene_set.shadow_alpha * 255))
        draw.ellipse(
            (
                feet_cx - rx,
                feet_y - ry + scene_set.shadow_dy,
                feet_cx + rx,
                feet_y + ry + scene_set.shadow_dy,
            ),
            fill=(0, 0, 0, alpha),
        )
        return mask.filter(ImageFilter.GaussianBlur(radius=scene_set.shadow_blur // 2))

    @staticmethod
    def build_grain_filter(grain_strength: float = 0.02) -> str:
        """Rule 5: Hoà hạt nhiễu (L7) - blends background and presenter into one unified frame."""
        noise_amt = int(round(grain_strength * 100))
        if noise_amt <= 0:
            return "null"
        return f"noise=alls={noise_amt}:allf=t+u"
