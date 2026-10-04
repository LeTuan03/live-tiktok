"""Filler and standby clip generator for graceful live recovery without terminating streams."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

from loguru import logger
from PIL import Image, ImageDraw

from app.integrations.ffmpeg.ffprobe_service import FFProbeService
from app.render.banner import get_font


DEFAULT_FILLER_DIR = Path("storage/filler")


def generate_standby_image(
    output_path: Path | str,
    title: str = "Đang chuẩn bị sản phẩm tiếp theo",
    subtitle: str = "Livestream vẫn đang tiếp tục, quý khách vui lòng chờ trong giây lát!",
    shop_name: str = "LiveFlow Official",
    width: int = 1080,
    height: int = 1920,
) -> Path:
    """Generate a high-quality vertical 1080x1920 standby background card."""
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    img = Image.new("RGB", (width, height), (16, 18, 26))
    draw = ImageDraw.Draw(img)

    # 1. Subtle background glow / decorative elements
    draw.ellipse((width // 4, height // 3, width * 3 // 4, height * 2 // 3), fill=(26, 32, 50))
    draw.ellipse((width // 3, height // 2 - 100, width * 2 // 3, height // 2 + 300), fill=(35, 25, 45))

    # 2. Top Header Pill
    top_y = 60
    header_font = get_font(30, bold=True)
    live_badge_font = get_font(22, bold=True)

    header_rect = (40, top_y, 440, top_y + 70)
    draw.rounded_rectangle(header_rect, radius=35, fill=(24, 27, 38), outline=(255, 255, 255, 40), width=2)
    # Live pill
    draw.rounded_rectangle((50, top_y + 12, 135, top_y + 58), radius=16, fill=(235, 40, 60))
    draw.ellipse((60, top_y + 24, 74, top_y + 38), fill=(255, 255, 255))
    draw.text((82, top_y + 19), "LIVE", font=live_badge_font, fill=(255, 255, 255))
    draw.text((150, top_y + 18), shop_name, font=header_font, fill=(255, 255, 255))

    # 3. Center Standby Card
    card_w = width - 120
    card_h = 520
    card_x = 60
    card_y = (height - card_h) // 2

    card_rect = (card_x, card_y, card_x + card_w, card_y + card_h)
    draw.rounded_rectangle(card_rect, radius=32, fill=(22, 25, 36), outline=(255, 255, 255, 50), width=2)

    # Standby pulse icon (drawn circles)
    center_x = width // 2
    icon_y = card_y + 90
    draw.ellipse((center_x - 45, icon_y - 45, center_x + 45, icon_y + 45), fill=(254, 44, 85, 40))
    draw.ellipse((center_x - 30, icon_y - 30, center_x + 30, icon_y + 30), fill=(254, 44, 85, 120))
    draw.ellipse((center_x - 16, icon_y - 16, center_x + 16, icon_y + 16), fill=(254, 44, 85, 255))

    # Main text
    title_font = get_font(42, bold=True)
    title_bbox = draw.textbbox((0, 0), title, font=title_font)
    title_w = title_bbox[2] - title_bbox[0]
    draw.text((center_x - title_w // 2, card_y + 180), title, font=title_font, fill=(255, 255, 255))

    sub_font = get_font(26, bold=False)
    sub_bbox = draw.textbbox((0, 0), subtitle, font=sub_font)
    sub_w = sub_bbox[2] - sub_bbox[0]
    draw.text((center_x - sub_w // 2, card_y + 260), subtitle, font=sub_font, fill=(180, 185, 200))

    # CTA Pill
    cta_rect = (card_x + 40, card_y + card_h - 110, card_x + card_w - 40, card_y + card_h - 40)
    draw.rounded_rectangle(cta_rect, radius=20, fill=(35, 40, 58), outline=(255, 255, 255, 30), width=1)
    cta_font = get_font(24, bold=True)
    cta_text = "👉 Đừng quên bấm giỏ hàng để xem các ưu đãi độc quyền!"
    cta_bbox = draw.textbbox((0, 0), cta_text, font=cta_font)
    cta_w = cta_bbox[2] - cta_bbox[0]
    draw.text((center_x - cta_w // 2, card_y + card_h - 88), cta_text, font=cta_font, fill=(255, 215, 0))

    img.save(str(target), format="PNG")
    return target


def create_filler_clip(
    output_mp4: Optional[Path | str] = None,
    duration_seconds: int = 6,
) -> Path:
    """Create a 1080x1920 30fps MP4 loop with silent audio to serve as broadcast filler."""
    if output_mp4 is None:
        target_mp4 = DEFAULT_FILLER_DIR / "filler_standby.mp4"
    else:
        target_mp4 = Path(output_mp4)

    target_mp4.parent.mkdir(parents=True, exist_ok=True)

    # 1. Generate standby image first
    img_path = target_mp4.with_suffix(".png")
    generate_standby_image(img_path)

    # 2. Check if valid MP4 already exists
    if target_mp4.exists() and target_mp4.stat().st_size > 1000:
        return target_mp4

    # 3. Use ffmpeg to encode image into 1080x1920 MP4 video with silent audio track
    ffprobe_svc = FFProbeService()
    ffmpeg_exe = ffprobe_svc._ffmpeg_path

    try:
        cmd = [
            ffmpeg_exe,
            "-y",
            "-loop", "1",
            "-i", str(img_path),
            "-f", "lavfi",
            "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-c:v", "libx264",
            "-t", str(duration_seconds),
            "-pix_fmt", "yuv420p",
            "-vf", "scale=1080:1920",
            "-r", "30",
            "-c:a", "aac",
            "-b:a", "128k",
            "-shortest",
            str(target_mp4),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and target_mp4.exists() and target_mp4.stat().st_size > 0:
            logger.info("Đã tạo video filler dự phòng thành công: {}", target_mp4)
            return target_mp4
        else:
            logger.warning("Lỗi tạo filler mp4 qua ffmpeg: {}. Dùng ảnh standby làm fallback.", result.stderr)
            return img_path
    except Exception as e:
        logger.warning("Không thể gọi ffmpeg để sinh clip filler: {}. Dùng ảnh standby.", e)
        return img_path


# Alias for backward-compatible and semantic playout calls
ensure_filler_clip = create_filler_clip

