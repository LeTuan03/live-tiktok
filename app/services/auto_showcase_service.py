"""Auto Showcase Service — Automated Product Video Generation & Livestream Orchestration.

Implements the 3-step automated flow:
1. Product Information Management
2. Automated Showcase Video Generation with Talking AI Presenter & Live Shopping Overlay
3. Automated Playlist Synchronization for continuous hands-free Livestreaming
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
from loguru import logger
from PIL import Image, ImageDraw, ImageFont

from app.config.settings import get_settings
from app.database.models.avatar import AIAvatar
from app.database.models.playlist import Playlist, PlaylistItem
from app.database.models.product import Product
from app.database.session import get_session
from app.render.avatar_animator import AvatarAnimator


class AutoShowcaseService:
    """Service to automatically generate talking presenter product videos and livestream playlists."""

    def __init__(self, session=None) -> None:
        self.session = session or get_session()
        self.settings = get_settings()
        self.ffmpeg_cmd = self.settings.ffmpeg_path or "ffmpeg"
        self.animator = AvatarAnimator(ffmpeg_bin=self.ffmpeg_cmd)

    # -------------------------------------------------------------------------
    # 1. Pitch Script Generation
    # -------------------------------------------------------------------------
    def generate_product_pitch(self, product: Product, avatar_name: str = "em") -> str:
        """Create an engaging, persuasive Vietnamese livestream pitch script for the product."""
        first_name = avatar_name.split("—")[0].strip().split()[0] if avatar_name else "em"

        name = product.name or "Sản phẩm cao cấp"
        price_str = f"{product.price:,.0f} đồng".replace(",", ".") if product.price else "giá cực tốt"
        promo_str = f"{product.promo_price:,.0f} đồng".replace(",", ".") if product.promo_price else None

        desc = (product.description or "").strip()
        features = (product.verified_features or "").strip()

        # Extract 1-2 highlight sentences from description/features
        highlight = ""
        if features:
            highlight = features.replace("\n", ". ")[:120]
        elif desc:
            highlight = desc.replace("\n", ". ")[:120]

        if highlight and not highlight.endswith("."):
            highlight += "."

        # Build dynamic sales pitch
        if promo_str:
            deal_sentence = (
                f"Giá niêm yết của hãng là {price_str}, nhưng duy nhất trong phiên livestream hôm nay, "
                f"{first_name} xin gửi tặng cả nhà mình với giá sốc chỉ còn {promo_str} thôi ạ!"
            )
        else:
            deal_sentence = f"Hiện tại siêu phẩm này đang có giá ưu đãi cực kỳ hấp dẫn là {price_str} ạ!"

        pitch = (
            f"Dạ {first_name} xin chào tất cả quý anh chị đang theo dõi livestream ạ! "
            f"Hôm nay {first_name} mang đến cho cả nhà mình siêu phẩm {name}. "
            f"{highlight} "
            f"{deal_sentence} "
            f"Số lượng ưu đãi trong kho có hạn, cả nhà mình nhanh tay bấm vào giỏ hàng ở góc màn hình để chốt đơn ngay nhé!"
        )

        return pitch.strip()

    # -------------------------------------------------------------------------
    # 2. TTS Voice Synthesis
    # -------------------------------------------------------------------------
    async def synthesize_speech(
        self,
        text: str,
        voice_id: str,
        output_audio_path: str | Path,
    ) -> str:
        """Generate Vietnamese TTS audio for the pitch with gTTS fallback."""
        out_path = Path(output_audio_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        voice = voice_id or "vi-VN-HoaiMyNeural"

        # Try Edge-TTS first
        try:
            cmd = [
                ".venv\\Scripts\\python.exe",
                "-m",
                "edge_tts",
                "--voice",
                voice,
                "--text",
                text,
                "--write-media",
                str(out_path),
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            await proc.communicate()
            if out_path.exists() and out_path.stat().st_size > 500:
                logger.info("Đã tạo giọng đọc bằng Edge-TTS: {}", out_path.name)
                return str(out_path)
        except Exception as e:
            logger.warning("Edge-TTS gặp lỗi, chuyển sang gTTS: {}", e)

        # Fallback to gTTS (Google Vietnamese Voice)
        try:
            from gtts import gTTS
            logger.info("Sử dụng gTTS tiếng Việt cho: {}", out_path.name)
            tts = await asyncio.to_thread(gTTS, text=text, lang="vi")
            await asyncio.to_thread(tts.save, str(out_path))
            if out_path.exists() and out_path.stat().st_size > 500:
                logger.info("Đã tạo giọng đọc bằng gTTS thành công: {}", out_path.name)
                return str(out_path)
        except Exception as e:
            logger.error("Lỗi gTTS: {}", e)
            raise RuntimeError(f"Không thể tạo âm thanh giọng đọc: {e}")

        return str(out_path)

    # -------------------------------------------------------------------------
    # 3. Full 9:16 Composite Video Rendering
    # -------------------------------------------------------------------------
    def _get_font(self, font_name: str = "segoeui.ttf", size: int = 32) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        """Load a system TrueType font with full Vietnamese Unicode support."""
        windir = os.environ.get("WINDIR", "C:\\Windows")
        for candidate in [font_name, "segoeuib.ttf", "arial.ttf", "arialbd.ttf", "tahoma.ttf"]:
            p = os.path.join(windir, "Fonts", candidate)
            if os.path.exists(p):
                try:
                    return ImageFont.truetype(p, size)
                except Exception:
                    pass
        return ImageFont.load_default()

    def _render_canvas_background(
        self,
        W: int = 1080,
        H: int = 1920,
        product: Optional[Product] = None,
        product_image: Optional[Image.Image] = None,
    ) -> Image.Image:
        """Render the base 1080x1920 live commerce layout frame."""
        canvas = Image.new("RGBA", (W, H), (14, 16, 24, 255))
        draw = ImageDraw.Draw(canvas)

        # 1. Gradient backdrop
        for y in range(0, H, 4):
            factor = y / float(H)
            r = int(14 + 18 * factor)
            g = int(16 + 22 * factor)
            b = int(26 + 32 * factor)
            draw.line([(0, y), (W, y)], fill=(r, g, b, 255), width=4)

        # 2. Header Bar: LIVE badge
        # Glowing top header
        draw.rounded_rectangle([(40, 50), (200, 110)], radius=16, fill=(225, 29, 72, 255))
        # Draw live indicator circle
        draw.ellipse([(60, 71), (78, 89)], fill=(255, 255, 255, 255))
        draw.ellipse([(64, 75), (74, 85)], fill=(225, 29, 72, 255))
        f_badge = self._get_font("segoeuib.ttf", 30)
        draw.text((92, 62), "LIVE", font=f_badge, fill=(255, 255, 255))

        f_header = self._get_font("segoeui.ttf", 26)
        draw.text((230, 67), "TIKTOK SHOPPING • SIÊU DEAL HÔM NAY", font=f_header, fill=(220, 230, 245))

        # 3. Product Showcase Card Container (y: 130 to 1050)
        card_x1, card_y1, card_x2, card_y2 = 50, 130, W - 50, 1050
        draw.rounded_rectangle(
            [(card_x1, card_y1), (card_x2, card_y2)],
            radius=28,
            fill=(24, 28, 42, 255),
            outline=(60, 70, 100, 255),
            width=2,
        )

        # Draw Product Image if available
        if product_image:
            max_img_w = (card_x2 - card_x1) - 60
            max_img_h = (card_y2 - card_y1) - 60
            img_copy = product_image.copy()
            img_copy.thumbnail((max_img_w, max_img_h), Image.Resampling.LANCZOS)
            offset_x = card_x1 + (card_x2 - card_x1 - img_copy.width) // 2
            offset_y = card_y1 + (card_y2 - card_y1 - img_copy.height) // 2
            canvas.paste(img_copy, (offset_x, offset_y))
        else:
            # Elegant placeholder card with product name & category
            f_cat = self._get_font("segoeuib.ttf", 38)
            cat_text = (product.category or "SẢN PHẨM CAO CẤP").upper() if product else "SẢN PHẨM CAO CẤP"
            draw.text((card_x1 + 60, card_y1 + 380), cat_text, font=f_cat, fill=(180, 205, 245))
            f_sub = self._get_font("segoeui.ttf", 28)
            draw.text((card_x1 + 60, card_y1 + 440), "Sản phẩm chính hãng phân phối độc quyền", font=f_sub, fill=(130, 145, 175))

        # 4. Lower Price & Details Card (y: 1080 to 1340)
        info_x1, info_y1, info_x2, info_y2 = 50, 1080, W - 50, 1340
        draw.rounded_rectangle(
            [(info_x1, info_y1), (info_x2, info_y2)],
            radius=24,
            fill=(20, 24, 38, 255),
            outline=(244, 63, 94, 255),
            width=3,
        )

        if product:
            f_name = self._get_font("segoeuib.ttf", 34)
            # Truncate / wrap name
            pname = product.name or ""
            if len(pname) > 48:
                line1 = pname[:46]
                line2 = pname[46:90] + ("..." if len(pname) > 90 else "")
                draw.text((info_x1 + 30, info_y1 + 25), line1, font=f_name, fill=(255, 255, 255))
                draw.text((info_x1 + 30, info_y1 + 70), line2, font=f_name, fill=(255, 255, 255))
            else:
                draw.text((info_x1 + 30, info_y1 + 35), pname, font=f_name, fill=(255, 255, 255))

            # Prices
            f_price = self._get_font("segoeuib.ttf", 52)
            f_old = self._get_font("segoeui.ttf", 30)

            if product.promo_price and product.price and product.promo_price < product.price:
                promo_txt = f"{product.promo_price:,.0f}₫".replace(",", ".")
                draw.text((info_x1 + 30, info_y1 + 130), promo_txt, font=f_price, fill=(250, 204, 21))  # Amber/Yellow

                old_txt = f"Gốc: {product.price:,.0f}₫".replace(",", ".")
                draw.text((info_x1 + 360, info_y1 + 148), old_txt, font=f_old, fill=(140, 150, 175))

                # Discount percentage badge
                discount = int(round((1.0 - (product.promo_price / product.price)) * 100))
                draw.rounded_rectangle(
                    [(info_x1 + 30, info_y1 + 195), (info_x1 + 160, info_y1 + 235)],
                    radius=10,
                    fill=(225, 29, 72),
                )
                f_disc = self._get_font("segoeuib.ttf", 22)
                draw.text((info_x1 + 45, info_y1 + 203), f"GIẢM -{discount}%", font=f_disc, fill=(255, 255, 255))

            elif product.price or product.promo_price:
                price_val = product.promo_price or product.price
                price_txt = f"{price_val:,.0f}₫".replace(",", ".")
                draw.text((info_x1 + 30, info_y1 + 130), price_txt, font=f_price, fill=(250, 204, 21))

            # Call to Action Pill
            draw.rounded_rectangle(
                [(info_x1 + 180, info_y1 + 195), (info_x2 - 30, info_y1 + 242)],
                radius=14,
                fill=(234, 179, 8),  # Gold/Yellow
            )
            f_cta = self._get_font("segoeuib.ttf", 24)
            draw.text((info_x1 + 205, info_y1 + 204), "BẤM GIỎ HÀNG GÓC TRÁI ĐẶT HÀNG NGAY", font=f_cta, fill=(20, 20, 20))

        return canvas

    async def render_product_showcase_video(
        self,
        product: Product,
        avatar: AIAvatar,
        pitch_text: str = "",
        progress_callback: Optional[Callable[[int, str], None]] = None,
    ) -> str:
        """Render complete, broadcast-ready 9:16 vertical livestream showcase video for a product.

        Combines:
        - Product image & branding
        - Live shopping overlay (prices, discounts, badges)
        - Animated talking AI presenter (with Speech Lip-Sync, head nodding, blinking)
        - Synchronized Vietnamese audio narration
        """
        W, H = 1080, 1920
        fps = 25

        if progress_callback:
            progress_callback(10, "Đang chuẩn bị kịch bản và giọng đọc...")

        script_text = pitch_text.strip() if pitch_text else self.generate_product_pitch(product, avatar.name)

        # 1. Synthesize audio
        voice_id = avatar.voice_id or ("vi-VN-HoaiMyNeural" if avatar.gender == "female" else "vi-VN-NamMinhNeural")
        media_dir = self.settings.effective_media_dir
        safe_name = f"showcase_{product.id}_{int(time.time())}"
        audio_path = media_dir / f"{safe_name}.mp3"
        await self.synthesize_speech(script_text, voice_id, audio_path)

        if progress_callback:
            progress_callback(30, "Đang trích xuất dữ liệu âm thanh và nhịp khẩu hình...")

        # 2. Extract audio PCM to compute speech volume / lip-sync energy per frame
        temp_pcm = media_dir / f"{safe_name}.pcm"
        cmd_pcm = [
            self.ffmpeg_cmd,
            "-y",
            "-i", str(audio_path),
            "-ac", "1",
            "-ar", "16000",
            "-f", "s16le",
            str(temp_pcm),
        ]
        subprocess.run(cmd_pcm, capture_output=True, check=True)

        try:
            pcm_bytes = temp_pcm.read_bytes()
            pcm = np.frombuffer(pcm_bytes, dtype=np.int16)
        finally:
            if temp_pcm.exists():
                try:
                    temp_pcm.unlink()
                except OSError:
                    pass

        samples_per_frame = 16000 // fps
        num_frames = max(1, len(pcm) // samples_per_frame)

        energies = []
        for i in range(num_frames):
            chunk = pcm[i * samples_per_frame : (i + 1) * samples_per_frame]
            rms = np.sqrt(np.mean(chunk.astype(float) ** 2)) if len(chunk) else 0.0
            energies.append(rms)

        energies = np.array(energies)
        max_e = np.percentile(energies, 95) if len(energies) else 1.0
        max_e = max(max_e, 500.0)
        norm_e = np.clip(energies / max_e, 0.0, 1.0)

        # Smooth energy profile
        smoothed = np.zeros_like(norm_e)
        for i in range(len(norm_e)):
            if i == 0:
                smoothed[i] = norm_e[i]
            else:
                if norm_e[i] > smoothed[i - 1]:
                    smoothed[i] = 0.55 * smoothed[i - 1] + 0.45 * norm_e[i]
                else:
                    smoothed[i] = 0.65 * smoothed[i - 1] + 0.35 * norm_e[i]

        if progress_callback:
            progress_callback(50, "Đang dựng khung cảnh Livestream và Người Mẫu AI...")

        # 3. Load product image
        prod_img = None
        if product.image_path and os.path.exists(product.image_path):
            try:
                prod_img = Image.open(product.image_path).convert("RGBA")
            except Exception as e:
                logger.warning("Không thể đọc ảnh sản phẩm {}: {}", product.image_path, e)

        # 4. Prepare base canvas
        base_canvas = self._render_canvas_background(W, H, product, prod_img)

        # 5. Load avatar image and prepare green-screen keying
        avatar_img_path = avatar.preview_image_path or avatar.media_path or ""
        if avatar_img_path.lower().endswith(".mp4"):
            candidate = Path(avatar_img_path).with_suffix(".jpg")
            if candidate.exists():
                avatar_img_path = str(candidate)

        if not os.path.exists(avatar_img_path):
            raise FileNotFoundError(f"Không tìm thấy ảnh người mẫu AI: {avatar_img_path}")

        avatar_pil = Image.open(avatar_img_path).convert("RGB")
        # Target avatar height 1120px for commanding host presence
        target_avatar_h = 1120
        target_avatar_w = int(target_avatar_h * (avatar_pil.width / float(avatar_pil.height)))
        avatar_pil = avatar_pil.resize((target_avatar_w, target_avatar_h), Image.Resampling.LANCZOS)
        avatar_arr = np.array(avatar_pil)
        profile = self.animator._get_avatar_profile(avatar_img_path, target_avatar_w, target_avatar_h)

        avatar_pos_x = W - target_avatar_w + 20
        avatar_pos_y = H - target_avatar_h

        # 6. Stream frames into FFmpeg
        out_video_path = media_dir / f"{safe_name}.mp4"

        cmd_ff = [
            self.ffmpeg_cmd,
            "-y",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-s", f"{W}x{H}",
            "-pix_fmt", "rgb24",
            "-r", str(fps),
            "-i", "-",
            "-i", str(audio_path),
            "-c:v", "libx264",
            "-preset", "faster",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            str(out_video_path),
        ]

        proc = subprocess.Popen(
            cmd_ff,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        try:
            for i in range(num_frames):
                e = float(smoothed[i])

                # Animate avatar frame (speaking, nodding, breathing, blinking)
                av_frame = avatar_arr.copy()

                # 1. Dynamic Breathing, Body Sway & Speech Gesturing
                sway = int(round(7.0 * np.sin(2 * np.pi * i / 75.0)))
                breathe = int(round(4.5 * np.sin(2 * np.pi * i / 50.0)))
                nod = int(round(e * 10.0))

                dy = breathe + nod
                if dy != 0:
                    av_frame = np.roll(av_frame, dy, axis=0)
                if sway != 0:
                    av_frame = np.roll(av_frame, sway, axis=1)

                # 2. Periodic natural eye blink (every ~3.2s)
                blink_closure = 0.0
                cycle_pos = (i % int(fps * 3.2)) / float(fps)
                if cycle_pos < 0.18:
                    blink_closure = 1.0 - abs(cycle_pos - 0.09) / 0.09

                if blink_closure > 0.05:
                    av_frame = self.animator._apply_blink(
                        av_frame,
                        profile["eyes"],
                        profile["eye_size"],
                        blink_closure,
                    )

                # 3. Voice-driven mouth opening (Lip-Sync with teeth & inner oral depth)
                if e > 0.05:
                    av_frame = self.animator._apply_mouth_opening(
                        av_frame,
                        profile["mouth"],
                        profile["mouth_w"],
                        int(profile["max_drop"] * 1.4),
                        e,
                    )

                # Chroma key green screen (#00FF00) into transparency
                r = av_frame[:, :, 0]
                g = av_frame[:, :, 1]
                b = av_frame[:, :, 2]
                is_green = (g > 135) & (g > r.astype(int) + 25) & (g > b.astype(int) + 25)

                av_rgba = np.dstack([av_frame, (~is_green * 255).astype(np.uint8)])
                av_pil = Image.fromarray(av_rgba, mode="RGBA")

                # Composite onto base canvas
                frame_canvas = base_canvas.copy()
                frame_canvas.paste(av_pil, (avatar_pos_x, avatar_pos_y), av_pil)

                rgb_frame = frame_canvas.convert("RGB")
                proc.stdin.write(rgb_frame.tobytes())

                if progress_callback and i % 25 == 0:
                    pct = 50 + int((i / float(num_frames)) * 45)
                    progress_callback(pct, f"Đang render khung hình {i}/{num_frames}...")

            proc.stdin.close()
            proc.wait()

            if not out_video_path.exists():
                raise RuntimeError("FFmpeg không tạo được tệp video đích.")

            # Update product database record
            product.video_path = str(out_video_path)
            self.session.commit()

            if progress_callback:
                progress_callback(100, "Hoàn tất tạo video giới thiệu sản phẩm!")

            logger.info("Đã tạo video giới thiệu sản phẩm: {} → {}", product.name, out_video_path)
            return str(out_video_path)

        except Exception as e:
            if proc and proc.stdin:
                proc.stdin.close()
            logger.exception("Lỗi khi render video giới thiệu: {}", e)
            raise

    # -------------------------------------------------------------------------
    # 4. Batch Generation for All Products
    # -------------------------------------------------------------------------
    async def batch_generate_showcase_videos(
        self,
        product_ids: List[int],
        avatar_id: int,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> List[str]:
        """Generate showcase videos for a list of products sequentially."""
        from app.database.models.avatar import AIAvatar

        avatar = self.session.query(AIAvatar).filter_by(id=avatar_id).first()
        if not avatar:
            raise ValueError(f"Không tìm thấy người mẫu AI id={avatar_id}")

        results: List[str] = []
        total = len(product_ids)

        for idx, pid in enumerate(product_ids, 1):
            product = self.session.query(Product).filter_by(id=pid).first()
            if not product:
                continue

            if progress_callback:
                progress_callback(idx, total, f"[{idx}/{total}] Đang xử lý: {product.name}")

            try:
                vid = await self.render_product_showcase_video(product, avatar)
                results.append(vid)
            except Exception as e:
                logger.error("Lỗi tạo video cho sản phẩm id={}: {}", pid, e)

        # After generating all videos, automatically sync to livestream playlist!
        self.sync_to_livestream_playlist()

        return results

    # -------------------------------------------------------------------------
    # 5. Livestream Playlist Synchronization
    # -------------------------------------------------------------------------
    def sync_to_livestream_playlist(self, playlist_name: str = "Livestream Tự Động AI") -> Playlist:
        """Collect all active products with showcase videos and sync into an automated livestream playlist."""
        playlist = (
            self.session.query(Playlist).filter_by(name=playlist_name).first()
        )
        if not playlist:
            playlist = Playlist(
                name=playlist_name,
                description="Danh sách phát video giới thiệu sản phẩm tự động do AI Người Mẫu tạo ra.",
                is_loop=True,
                loop_count=999,
                gap_seconds=2.0,
                is_active=True,
            )
            self.session.add(playlist)
            self.session.commit()

        # Remove existing items
        self.session.query(PlaylistItem).filter_by(playlist_id=playlist.id).delete()

        # Find all active products with valid video_path
        products = (
            self.session.query(Product)
            .filter(Product.is_active.is_(True), Product.video_path.isnot(None))
            .all()
        )

        valid_products = [p for p in products if p.video_path and os.path.exists(p.video_path)]

        for order, prod in enumerate(valid_products):
            item = PlaylistItem(
                playlist_id=playlist.id,
                product_id=prod.id,
                sort_order=order,
                is_enabled=True,
                notes=f"Video AI: {prod.name}",
            )
            self.session.add(item)

        self.session.commit()
        logger.info(
            "Đã đồng bộ {} sản phẩm có video vào playlist '{}'",
            len(valid_products),
            playlist_name,
        )
        return playlist
