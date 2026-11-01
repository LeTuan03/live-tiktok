"""First-Frame Composite Preprocessor for Veo 3 Video Generation.

Combines AI Avatar and Product into a vertical 9:16 initial frame,
giving Veo a concrete visual starting point to animate natural interaction.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageOps
from loguru import logger

from app.config.settings import get_settings


class VeoPreprocessor:
    """Prepares reference composite frames for Image-to-Video generation."""

    CANVAS_WIDTH = 1080
    CANVAS_HEIGHT = 1920

    @classmethod
    def create_first_frame(
        cls,
        avatar_image_path: Optional[str | Path],
        product_image_path: Optional[str | Path],
        shot_type: str = "HOOK",
        output_path: Optional[str | Path] = None,
    ) -> str:
        """Create a 9:16 composite frame containing avatar and product.

        Args:
            avatar_image_path: Path to avatar portrait/photo.
            product_image_path: Path to product image.
            shot_type: 'HOOK', 'FEATURE_DEMO', or 'CTA'.
            output_path: Target path for the output JPEG file.

        Returns:
            Absolute path to composite image.
        """
        settings = get_settings()
        if not output_path:
            import uuid
            output_path = settings.effective_veo_dir / f"frame_{uuid.uuid4().hex[:10]}.jpg"
        else:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

        # 1. Base Studio Background
        base = Image.new("RGB", (cls.CANVAS_WIDTH, cls.CANVAS_HEIGHT), color=(26, 28, 38))
        draw = ImageDraw.Draw(base)

        # Draw soft gradient / studio glow
        for y in range(cls.CANVAS_HEIGHT):
            ratio = y / cls.CANVAS_HEIGHT
            r = int(22 + 20 * ratio)
            g = int(24 + 16 * ratio)
            b = int(38 + 25 * ratio)
            draw.line([(0, y), (cls.CANVAS_WIDTH, y)], fill=(r, g, b))

        # 2. Place Avatar
        avatar_img = None
        if avatar_image_path and Path(avatar_image_path).exists():
            try:
                avatar_img = Image.open(avatar_image_path).convert("RGBA")
            except Exception as e:
                logger.warning("Không thể mở ảnh avatar ({}): {}", avatar_image_path, e)

        # 3. Place Product
        product_img = None
        if product_image_path and Path(product_image_path).exists():
            try:
                product_img = Image.open(product_image_path).convert("RGBA")
            except Exception as e:
                logger.warning("Không thể mở ảnh sản phẩm ({}): {}", product_image_path, e)

        # Render layout according to shot_type
        if shot_type == "FEATURE_DEMO":
            cls._layout_demo(base, avatar_img, product_img)
        elif shot_type == "CTA":
            cls._layout_cta(base, avatar_img, product_img)
        else:  # HOOK & general
            cls._layout_hook(base, avatar_img, product_img)

        # Convert to RGB and save high quality JPEG
        final_rgb = base.convert("RGB")
        final_rgb.save(output_path, "JPEG", quality=92)
        logger.info("Đã tạo frame tham chiếu Veo [{}] tại: {}", shot_type, output_path)
        return str(output_path)

    @classmethod
    def _layout_hook(
        cls,
        base: Image.Image,
        avatar_img: Optional[Image.Image],
        product_img: Optional[Image.Image],
    ) -> None:
        """Hook shot: Avatar in center/half body, product in front right."""
        if avatar_img:
            # Scale avatar to fit canvas nicely (e.g. 1080 x 1440)
            av_w = int(cls.CANVAS_WIDTH * 0.95)
            av_h = int(avatar_img.height * (av_w / avatar_img.width))
            resized_av = avatar_img.resize((av_w, av_h), Image.Resampling.LANCZOS)
            x = (cls.CANVAS_WIDTH - av_w) // 2
            y = max(100, cls.CANVAS_HEIGHT - av_h - 150)
            base.paste(resized_av, (x, y), mask=resized_av if resized_av.mode == "RGBA" else None)

        if product_img:
            # Scale product nicely into hand/lower right corner
            p_w = int(cls.CANVAS_WIDTH * 0.38)
            p_h = int(product_img.height * (p_w / product_img.width))
            resized_p = product_img.resize((p_w, p_h), Image.Resampling.LANCZOS)
            px = int(cls.CANVAS_WIDTH * 0.56)
            py = int(cls.CANVAS_HEIGHT * 0.52)
            # Add subtle shadow
            cls._paste_with_shadow(base, resized_p, px, py)

    @classmethod
    def _layout_demo(
        cls,
        base: Image.Image,
        avatar_img: Optional[Image.Image],
        product_img: Optional[Image.Image],
    ) -> None:
        """Demo shot: Product close up in center, presenter in background."""
        if avatar_img:
            # Soft blurred background presenter
            av_w = cls.CANVAS_WIDTH
            av_h = int(avatar_img.height * (av_w / avatar_img.width))
            resized_av = avatar_img.resize((av_w, av_h), Image.Resampling.LANCZOS)
            # Slight blur to create depth of field
            blurred_av = resized_av.filter(ImageFilter.GaussianBlur(radius=3))
            base.paste(blurred_av, (0, 100), mask=blurred_av if blurred_av.mode == "RGBA" else None)

        if product_img:
            # Large product in center
            p_w = int(cls.CANVAS_WIDTH * 0.65)
            p_h = int(product_img.height * (p_w / product_img.width))
            resized_p = product_img.resize((p_w, p_h), Image.Resampling.LANCZOS)
            px = (cls.CANVAS_WIDTH - p_w) // 2
            py = int(cls.CANVAS_HEIGHT * 0.40)
            cls._paste_with_shadow(base, resized_p, px, py)

    @classmethod
    def _layout_cta(
        cls,
        base: Image.Image,
        avatar_img: Optional[Image.Image],
        product_img: Optional[Image.Image],
    ) -> None:
        """CTA shot: Presenter gesturing, product placed neatly."""
        if avatar_img:
            av_w = int(cls.CANVAS_WIDTH * 0.90)
            av_h = int(avatar_img.height * (av_w / avatar_img.width))
            resized_av = avatar_img.resize((av_w, av_h), Image.Resampling.LANCZOS)
            x = int(cls.CANVAS_WIDTH * 0.10)
            y = max(80, cls.CANVAS_HEIGHT - av_h - 180)
            base.paste(resized_av, (x, y), mask=resized_av if resized_av.mode == "RGBA" else None)

        if product_img:
            p_w = int(cls.CANVAS_WIDTH * 0.35)
            p_h = int(product_img.height * (p_w / product_img.width))
            resized_p = product_img.resize((p_w, p_h), Image.Resampling.LANCZOS)
            px = int(cls.CANVAS_WIDTH * 0.08)
            py = int(cls.CANVAS_HEIGHT * 0.50)
            cls._paste_with_shadow(base, resized_p, px, py)

    @classmethod
    def _paste_with_shadow(
        cls, base: Image.Image, item: Image.Image, x: int, y: int
    ) -> None:
        """Paste item with a soft drop shadow for realism."""
        if item.mode == "RGBA":
            # Extract alpha for shadow
            alpha = item.split()[-1]
            shadow = Image.new("RGBA", item.size, color=(0, 0, 0, 140))
            shadow.putalpha(alpha)
            shadow = shadow.filter(ImageFilter.GaussianBlur(radius=8))
            base.paste(shadow, (x + 8, y + 10), mask=shadow)
            base.paste(item, (x, y), mask=item)
        else:
            base.paste(item, (x, y))
