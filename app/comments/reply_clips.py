"""Reply Clip Manager — assembles NOD_LISTEN gesture clips with TTS audio into priority playout items (§3.6, §6 P5)."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Optional

from loguru import logger
from PIL import Image, ImageDraw

from app.integrations.ffmpeg.ffprobe_service import FFProbeService
from app.playout.engine import PlayoutEngine, PlayoutItem
from app.presenter.clip_library import PresenterClipLibrary
from app.render.banner import get_font
from app.services.voice_service import VoiceService

DEFAULT_REPLIES_STORAGE = Path("storage/replies")


class ReplyClipManager:
    """Combines NOD_LISTEN gesture video clips with TTS audio to form live interjection clips."""

    def __init__(
        self,
        storage_dir: Optional[Path | str] = None,
        voice_service: Optional[VoiceService] = None,
        clip_library: Optional[PresenterClipLibrary] = None,
    ) -> None:
        self.storage_dir = Path(storage_dir or DEFAULT_REPLIES_STORAGE)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.voice_service = voice_service
        self.clip_library = clip_library or PresenterClipLibrary()
        self.ffprobe_svc = FFProbeService()


    def generate_reply_card_image(
        self,
        output_png: Path | str,
        customer_name: str,
        reply_text: str,
        width: int = 1080,
        height: int = 1920,
    ) -> Path:
        """Create a 1080x1920 graphic card for the live reply if no video gesture is available."""
        target = Path(output_png)
        target.parent.mkdir(parents=True, exist_ok=True)

        img = Image.new("RGB", (width, height), (15, 18, 25))
        draw = ImageDraw.Draw(img)

        # Ambient lighting glows
        draw.ellipse((width // 4, height // 3, width * 3 // 4, height * 2 // 3), fill=(25, 30, 48))

        # Top Badge
        top_y = 120
        pill_rect = (width // 2 - 200, top_y, width // 2 + 200, top_y + 60)
        draw.rounded_rectangle(pill_rect, radius=30, fill=(254, 44, 85, 230))
        tag_font = get_font(26, bold=True)
        tag_text = "💬 TRẢ LỜI BÌNH LUẬN"
        t_bbox = draw.textbbox((0, 0), tag_text, font=tag_font)
        draw.text(
            (width // 2 - (t_bbox[2] - t_bbox[0]) // 2, top_y + 14),
            tag_text,
            font=tag_font,
            fill=(255, 255, 255),
        )

        # Center Reply Card
        card_w = width - 140
        card_h = 700
        card_x = 70
        card_y = (height - card_h) // 2

        draw.rounded_rectangle(
            (card_x, card_y, card_x + card_w, card_y + card_h),
            radius=32,
            fill=(24, 28, 40),
            outline=(255, 255, 255, 60),
            width=2,
        )

        # Customer Name Pill
        cust_font = get_font(34, bold=True)
        cust_text = f"Khách hàng: @{customer_name}"
        draw.text((card_x + 50, card_y + 60), cust_text, font=cust_font, fill=(254, 44, 85))

        # Reply Content (wrapped)
        reply_font = get_font(32, bold=False)
        # Simple word wrap
        words = reply_text.split()
        lines = []
        cur_line = ""
        for w in words:
            test_line = f"{cur_line} {w}".strip()
            bbox = draw.textbbox((0, 0), test_line, font=reply_font)
            if bbox[2] - bbox[0] > (card_w - 100):
                lines.append(cur_line)
                cur_line = w
            else:
                cur_line = test_line
        if cur_line:
            lines.append(cur_line)

        text_y = card_y + 160
        for line in lines[:8]:  # Max 8 lines
            draw.text((card_x + 50, text_y), line, font=reply_font, fill=(240, 245, 255))
            text_y += 50

        # Bottom hint
        hint_font = get_font(24, bold=True)
        draw.text(
            (card_x + 50, card_y + card_h - 70),
            "👉 Bấm vào giỏ hàng góc trái để chốt đơn ngay!",
            font=hint_font,
            fill=(255, 215, 0),
        )

        img.save(str(target), format="PNG")
        return target

    def render_reply_clip(
        self,
        reply_text: str,
        customer_name: str,
        avatar_id: Optional[int | str] = None,
        audio_path: Optional[Path | str] = None,
        duration: Optional[float] = None,
    ) -> Path:
        """Render a vertical 1080x1920 MP4 combining video/card + TTS audio in < 5 seconds."""
        timestamp = int(time.time() * 1000)
        output_mp4 = self.storage_dir / f"reply_{timestamp}.mp4"

        # 1. Synthesize audio if not supplied
        if audio_path is None or not Path(audio_path).exists():
            target_audio = self.storage_dir / f"reply_audio_{timestamp}.mp3"
            try:
                if self.voice_service is not None:
                    # Use VoiceService for clean TTS with volume & speed safeguards
                    audio_path = self.voice_service.synthesize_speech(
                        text=reply_text,
                        output_path=target_audio,
                    )
            except Exception as e:
                logger.warning("Không thể gọi TTS: {}. Sử dụng file âm thanh trống.", e)
                audio_path = None



        # Determine audio duration
        if audio_path and Path(audio_path).exists():
            audio_duration = self.ffprobe_svc.get_audio_duration(audio_path)
            clip_dur = max(audio_duration, 3.0)
        else:
            clip_dur = duration or 4.0

        # 2. Find gesture video clip (NOD_LISTEN preferred, fallback to IDLE_TALK)
        gesture_video = None
        if avatar_id is not None:
            gesture_clip = self.clip_library.get_clip_for_cue(avatar_id, "NOD_LISTEN", approved_only=True)
            if not gesture_clip:
                gesture_clip = self.clip_library.get_clip_for_cue(avatar_id, "IDLE_TALK", approved_only=True)
            if gesture_clip and Path(gesture_clip.file_path).exists():
                gesture_video = Path(gesture_clip.file_path)

        ffmpeg_exe = self.ffprobe_svc._ffmpeg_path

        # 3. Assemble via ffmpeg
        if gesture_video and gesture_video.exists():
            # Merge gesture video loop + TTS audio
            cmd = [
                ffmpeg_exe,
                "-y",
                "-stream_loop", "-1",
                "-i", str(gesture_video),
            ]
            if audio_path and Path(audio_path).exists():
                cmd.extend(["-i", str(audio_path)])
            else:
                cmd.extend(["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"])

            cmd.extend([
                "-t", str(clip_dur),
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-pix_fmt", "yuv420p",
                "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
                "-c:a", "aac",
                "-b:a", "128k",
                "-shortest",
                str(output_mp4),
            ])
        else:
            # Generate styled reply graphic card
            card_png = self.storage_dir / f"reply_card_{timestamp}.png"
            self.generate_reply_card_image(card_png, customer_name, reply_text)

            cmd = [
                ffmpeg_exe,
                "-y",
                "-loop", "1",
                "-i", str(card_png),
            ]
            if audio_path and Path(audio_path).exists():
                cmd.extend(["-i", str(audio_path)])
            else:
                cmd.extend(["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"])

            cmd.extend([
                "-t", str(clip_dur),
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-pix_fmt", "yuv420p",
                "-vf", "scale=1080:1920",
                "-c:a", "aac",
                "-b:a", "128k",
                "-shortest",
                str(output_mp4),
            ])

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if res.returncode == 0 and output_mp4.exists() and output_mp4.stat().st_size > 0:
                logger.info("Đã tạo clip phản hồi người xem thành công: {}", output_mp4)
                return output_mp4
            else:
                logger.error("FFmpeg render reply clip lỗi: {}", res.stderr)
        except Exception as e:
            logger.error("Ngoại lệ khi render reply clip: {}", e)

        # Fallback: create empty placeholder or return card image
        return output_mp4

    def enqueue_reply_to_engine(
        self,
        engine: PlayoutEngine,
        reply_text: str,
        customer_name: str,
        avatar_id: Optional[int | str] = None,
        product_id: Optional[int] = None,
    ) -> bool:
        """Render reply clip and inject as high-priority item into the active playout engine."""
        clip_path = self.render_reply_clip(
            reply_text=reply_text,
            customer_name=customer_name,
            avatar_id=avatar_id,
        )

        item = PlayoutItem(
            title=f"Trả lời @{customer_name}",
            video_path=str(clip_path),
            product_id=product_id,
            is_insertion=True,
            metadata={"customer": customer_name, "reply": reply_text},
        )

        engine.insert_priority_clip(item)
        logger.info("Đã nạp câu trả lời cho @{} vào hàng đợi ưu tiên của PlayoutEngine", customer_name)
        return True
