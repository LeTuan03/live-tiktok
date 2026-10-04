"""Banner generator for 9:16 vertical livestream overlay.

Renders high-quality glassmorphic overlay cards (top header and bottom product card)
using Pillow. Outputs a 1080x1920 RGBA PNG file with transparency, suitable for
direct inclusion in OBS Studio (image_source) or ffmpeg compositing.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image, ImageColor, ImageDraw, ImageFont


# Default font candidates on Windows, Linux, and MacOS
SYSTEM_FONT_CANDIDATES = [
    # Windows
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/tahomabd.ttf",
    "C:/Windows/Fonts/tahoma.ttf",
    # Linux
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    # MacOS
    "/System/Library/Fonts/SFProText-Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]


def get_font(size: int, bold: bool = False, custom_font_path: Optional[str] = None) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a system or custom font supporting Vietnamese unicode characters."""
    if custom_font_path and os.path.isfile(custom_font_path):
        try:
            return ImageFont.truetype(custom_font_path, size)
        except Exception:
            pass

    for candidate in SYSTEM_FONT_CANDIDATES:
        if bold and ("bd" in candidate.lower() or "bold" in candidate.lower()) and os.path.isfile(candidate):
            try:
                return ImageFont.truetype(candidate, size)
            except Exception:
                continue
        elif not bold and os.path.isfile(candidate):
            try:
                return ImageFont.truetype(candidate, size)
            except Exception:
                continue

    # Fallback to any existing font
    for candidate in SYSTEM_FONT_CANDIDATES:
        if os.path.isfile(candidate):
            try:
                return ImageFont.truetype(candidate, size)
            except Exception:
                continue

    return ImageFont.load_default()


def format_vnd(amount: Optional[float | int]) -> str:
    """Format an amount into Vietnamese standard currency display, e.g. 199.000₫."""
    if amount is None or amount <= 0:
        return "Liên hệ"
    int_amount = int(round(amount))
    formatted = f"{int_amount:,}".replace(",", ".")
    return f"{formatted}₫"


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    """Wrap text to fit within max_width pixels."""
    words = text.split()
    if not words:
        return [""]

    lines: list[str] = []
    current_line = []

    for word in words:
        test_line = " ".join(current_line + [word])
        bbox = draw.textbbox((0, 0), test_line, font=font)
        line_w = bbox[2] - bbox[0]
        if line_w <= max_width:
            current_line.append(word)
        else:
            if current_line:
                lines.append(" ".join(current_line))
                current_line = [word]
            else:
                lines.append(word)
                current_line = []

    if current_line:
        lines.append(" ".join(current_line))

    return lines


class BannerGenerator:
    """Renders professional 9:16 vertical livestream overlay banners."""

    def __init__(self, font_path: Optional[str] = None) -> None:
        self.font_path = font_path

    def render_overlay(
        self,
        product_name: str,
        price: Optional[float] = None,
        promo_price: Optional[float] = None,
        shop_name: str = "LiveFlow Shop",
        cta_text: str = "👉 Bấm giỏ hàng góc trái màn hình",
        is_live: bool = True,
        ai_generated_label: bool = False,
        width: int = 1080,
        height: int = 1920,
    ) -> Image.Image:
        """Create a full 1080x1920 transparent PNG overlay with modern shopping banner cards."""
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay, "RGBA")

        # -------------------------------------------------------------
        # TOP HEADER CARD (L6)
        # Position: near the top (e.g. y = 40 to 120)
        # -------------------------------------------------------------
        top_margin_x = 40
        top_y = 50
        card_h = 76

        # Draw top pill for shop name + live badge
        shop_font = get_font(28, bold=True, custom_font_path=self.font_path)
        badge_font = get_font(22, bold=True, custom_font_path=self.font_path)

        # Background container for header
        header_text = shop_name.strip() or "LiveFlow Shop"
        shop_bbox = draw.textbbox((0, 0), header_text, font=shop_font)
        shop_w = shop_bbox[2] - shop_bbox[0]

        pill_w = max(380, shop_w + 160)
        pill_rect = (top_margin_x, top_y, top_margin_x + pill_w, top_y + card_h)

        # Frosted glass card
        draw.rounded_rectangle(
            pill_rect,
            radius=card_h // 2,
            fill=(15, 17, 23, 215),
            outline=(255, 255, 255, 45),
            width=2,
        )

        # Live indicator pill inside header
        if is_live:
            live_rect = (top_margin_x + 10, top_y + 12, top_margin_x + 95, top_y + card_h - 12)
            draw.rounded_rectangle(live_rect, radius=18, fill=(235, 40, 60, 240))
            # Little white circle
            draw.ellipse((top_margin_x + 22, top_y + 24, top_margin_x + 36, top_y + 38), fill=(255, 255, 255, 255))
            draw.text((top_margin_x + 44, top_y + 19), "LIVE", font=badge_font, fill=(255, 255, 255, 255))

        shop_text_x = top_margin_x + (115 if is_live else 25)
        draw.text((shop_text_x, top_y + 18), header_text, font=shop_font, fill=(255, 255, 255, 250))

        # AI-generated content label if required by platform policy (§4.6)
        if ai_generated_label:
            ai_text = "AI-Generated / Nội dung có dùng AI"
            ai_font = get_font(20, bold=False, custom_font_path=self.font_path)
            ai_bbox = draw.textbbox((0, 0), ai_text, font=ai_font)
            ai_w = ai_bbox[2] - ai_bbox[0] + 30
            ai_rect = (width - top_margin_x - ai_w, top_y + 12, width - top_margin_x, top_y + card_h - 12)
            draw.rounded_rectangle(ai_rect, radius=16, fill=(30, 32, 40, 200), outline=(255, 255, 255, 30), width=1)
            draw.text((width - top_margin_x - ai_w + 15, top_y + 20), ai_text, font=ai_font, fill=(210, 215, 225, 230))

        # -------------------------------------------------------------
        # BOTTOM PRODUCT CARD (L4)
        # Position: pinned to lower section (e.g. y = 1420 to 1840)
        # Leaves margin for platform UI (chat, reactions, cart icon)
        # -------------------------------------------------------------
        card_margin_x = 36
        card_width = width - (card_margin_x * 2)
        bottom_y = 1460
        bottom_h = 360

        card_rect = (card_margin_x, bottom_y, card_margin_x + card_width, bottom_y + bottom_h)

        # Draw deep dark glassmorphic rounded card
        draw.rounded_rectangle(
            card_rect,
            radius=28,
            fill=(18, 20, 28, 230),
            outline=(255, 255, 255, 55),
            width=2,
        )

        inner_x = card_margin_x + 32
        content_y = bottom_y + 28
        inner_w = card_width - 64

        # 1. Product title (Wrapped up to 2 lines)
        title_font = get_font(42, bold=True, custom_font_path=self.font_path)
        wrapped_title_lines = wrap_text(draw, product_name, title_font, inner_w)
        # Limit to 2 lines max with ellipsis if longer
        if len(wrapped_title_lines) > 2:
            wrapped_title_lines = wrapped_title_lines[:2]
            wrapped_title_lines[1] = wrapped_title_lines[1][:40] + "..."

        for line in wrapped_title_lines:
            draw.text((inner_x, content_y), line, font=title_font, fill=(255, 255, 255, 255))
            bbox = draw.textbbox((inner_x, content_y), line, font=title_font)
            content_y += (bbox[3] - bbox[0]) + 12

        content_y += 10

        # 2. Price section
        price_y = content_y
        has_promo = promo_price is not None and promo_price > 0 and price is not None and promo_price < price

        if has_promo:
            # Promo price: prominent in vibrant TikTok gold/coral (#FFD200 or #FF2B54)
            promo_font = get_font(56, bold=True, custom_font_path=self.font_path)
            promo_str = format_vnd(promo_price)
            draw.text((inner_x, price_y), promo_str, font=promo_font, fill=(255, 210, 0, 255))
            promo_bbox = draw.textbbox((inner_x, price_y), promo_str, font=promo_font)
            promo_w = promo_bbox[2] - promo_bbox[0]

            # Original price (strikethrough)
            orig_font = get_font(34, bold=False, custom_font_path=self.font_path)
            orig_str = format_vnd(price)
            orig_x = inner_x + promo_w + 24
            orig_y = price_y + 16
            draw.text((orig_x, orig_y), orig_str, font=orig_font, fill=(160, 165, 175, 220))
            orig_bbox = draw.textbbox((orig_x, orig_y), orig_str, font=orig_font)
            strike_y = (orig_bbox[1] + orig_bbox[3]) // 2
            draw.line([(orig_x, strike_y), (orig_bbox[2], strike_y)], fill=(180, 185, 195, 240), width=3)

            # Discount percentage badge: e.g. "-35%"
            pct = int(round((1 - (promo_price / price)) * 100))
            if pct > 0:
                badge_pct_str = f"-{pct}%"
                badge_pct_font = get_font(26, bold=True, custom_font_path=self.font_path)
                badge_x = orig_bbox[2] + 20
                badge_y = orig_y - 4
                badge_bbox = draw.textbbox((0, 0), badge_pct_str, font=badge_pct_font)
                badge_w = badge_bbox[2] - badge_bbox[0] + 20
                badge_h = badge_bbox[3] - badge_bbox[1] + 12
                draw.rounded_rectangle(
                    (badge_x, badge_y, badge_x + badge_w, badge_y + badge_h),
                    radius=8,
                    fill=(235, 45, 65, 240),
                )
                draw.text((badge_x + 10, badge_y + 4), badge_pct_str, font=badge_pct_font, fill=(255, 255, 255, 255))

        elif price is not None and price > 0:
            regular_font = get_font(54, bold=True, custom_font_path=self.font_path)
            reg_str = format_vnd(price)
            draw.text((inner_x, price_y), reg_str, font=regular_font, fill=(255, 210, 0, 255))
        else:
            contact_font = get_font(44, bold=True, custom_font_path=self.font_path)
            draw.text((inner_x, price_y), "GIÁ ƯU ĐÃI TRÊN LIVE", font=contact_font, fill=(255, 210, 0, 255))

        # 3. Call to Action (CTA) bar at bottom of card
        cta_y = bottom_y + bottom_h - 76
        cta_h = 52
        cta_rect = (inner_x, cta_y, inner_x + inner_w, cta_y + cta_h)

        # Gradient-like background for CTA pill
        draw.rounded_rectangle(
            cta_rect,
            radius=14,
            fill=(254, 44, 85, 235),  # TikTok signature vibrant coral red
            outline=(255, 255, 255, 60),
            width=1,
        )

        cta_font = get_font(26, bold=True, custom_font_path=self.font_path)
        cta_text_str = cta_text.strip() or "👉 Bấm giỏ hàng góc trái màn hình"
        cta_bbox = draw.textbbox((0, 0), cta_text_str, font=cta_font)
        cta_w = cta_bbox[2] - cta_bbox[0]
        cta_text_x = inner_x + (inner_w - cta_w) // 2
        draw.text((cta_text_x, cta_y + 11), cta_text_str, font=cta_font, fill=(255, 255, 255, 255))

        return overlay

    def save_overlay(
        self,
        product_name: str,
        output_path: str | Path,
        price: Optional[float] = None,
        promo_price: Optional[float] = None,
        shop_name: str = "LiveFlow Shop",
        cta_text: str = "👉 Bấm giỏ hàng góc trái màn hình",
        is_live: bool = True,
        ai_generated_label: bool = False,
        width: int = 1080,
        height: int = 1920,
    ) -> Path:
        """Render and save transparent PNG banner to destination path."""
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        img = self.render_overlay(
            product_name=product_name,
            price=price,
            promo_price=promo_price,
            shop_name=shop_name,
            cta_text=cta_text,
            is_live=is_live,
            ai_generated_label=ai_generated_label,
            width=width,
            height=height,
        )
        img.save(str(target), format="PNG")
        return target
