"""Avatar Animator — Real-time presenter animation engine.

Generates living, breathing idle motion loops and audio-driven lip-sync
talking videos with natural head nodding, swaying, and eye blinking.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from loguru import logger
from PIL import Image


# Facial feature anchors for preset models (mouth_x, mouth_y, mouth_width, [(eye1_x, eye1_y), (eye2_x, eye2_y)])
_PRESET_FACE_DATA: Dict[str, Dict] = {
    "mai_phuong": {
        "mouth": (448, 360),
        "mouth_w": 45,
        "eyes": [(415, 280), (480, 280)],
        "eye_size": (26, 12),
        "max_drop": 15,
    },
    "thu_thao": {
        "mouth": (470, 329),
        "mouth_w": 48,
        "eyes": [(435, 254), (505, 254)],
        "eye_size": (28, 12),
        "max_drop": 16,
    },
    "minh_khoi": {
        "mouth": (430, 311),
        "mouth_w": 44,
        "eyes": [(395, 236), (465, 236)],
        "eye_size": (26, 12),
        "max_drop": 15,
    },
    "hai_dang": {
        "mouth": (360, 240),
        "mouth_w": 40,
        "eyes": [(335, 190), (385, 190)],
        "eye_size": (22, 10),
        "max_drop": 14,
    },
}


class AvatarAnimator:
    """Renders living motion and speech lip-sync animation from avatar portrait photos."""

    def __init__(self, ffmpeg_bin: str = "ffmpeg") -> None:
        self.ffmpeg_bin = ffmpeg_bin

    @staticmethod
    def _foreground_mask(arr: np.ndarray) -> Optional[np.ndarray]:
        """Tách mặt nạ người mẫu khỏi nền xanh lá / nền trong suốt.

        Trả về None nếu không nhận ra nền (ảnh chụp nền phức tạp) để bên gọi dùng
        phương án ước lượng theo tỉ lệ.
        """
        if arr.ndim != 3 or arr.shape[2] < 3:
            return None

        if arr.shape[2] == 4:
            alpha = arr[:, :, 3]
            # Chỉ tin kênh alpha khi nó thực sự có vùng trong suốt. Ảnh RGB bị convert
            # sang RGBA sẽ có alpha đục toàn bộ -> coi cả ảnh là người mẫu -> dò sai.
            if (alpha <= 128).any():
                mask = alpha > 128
                if mask.any():
                    return mask

        r = arr[:, :, 0].astype(np.int16)
        g = arr[:, :, 1].astype(np.int16)
        b = arr[:, :, 2].astype(np.int16)

        # Nền xanh lá: kênh G trội hẳn so với R và B
        green = (g > 90) & (g - r > 40) & (g - b > 40)
        if green.mean() < 0.12:  # không đủ nền xanh -> không đoán được
            return None

        mask = ~green
        return mask if mask.any() else None

    def _detect_head_box(self, arr: np.ndarray) -> Optional[Tuple[int, int, int]]:
        """Dò (đỉnh đầu, đáy đầu/cổ, tâm x của đầu) từ hình bóng người mẫu.

        Dựa vào biểu đồ bề rộng theo từng hàng: đầu phình ra, tới cổ thì thắt lại,
        xuống vai thì nở rộng đột ngột. Điểm thắt chính là đáy đầu.
        """
        mask = self._foreground_mask(arr)
        if mask is None:
            return None

        rows = mask.sum(axis=1)
        solid = np.flatnonzero(rows > max(2, int(0.01 * mask.shape[1])))
        if solid.size == 0:
            return None

        top_y, bottom_y = int(solid[0]), int(solid[-1])
        subject_h = bottom_y - top_y
        if subject_h < 40:
            return None

        # Cổ luôn nằm trong khoảng 10%-45% chiều cao người (kể cả ảnh bán thân lẫn toàn thân)
        lo = top_y + max(4, int(subject_h * 0.10))
        hi = top_y + max(lo - top_y + 4, int(subject_h * 0.45))
        hi = min(hi, bottom_y)
        if hi - lo < 4:
            return None

        band = rows[lo:hi]
        if int(band.max()) - int(band.min()) < 3:
            # Bề rộng phẳng lì -> không tách được nền, đừng đoán bừa.
            return None
        neck_y = lo + int(np.argmin(band))

        head_h = neck_y - top_y
        if head_h < 12 or head_h > subject_h * 0.6:
            return None

        # Tâm x lấy theo phần đầu, không lấy cả người (tránh lệch vì tay/vai)
        head_rows = mask[top_y:neck_y]
        xs = np.flatnonzero(head_rows.any(axis=0))
        if xs.size == 0:
            return None
        head_cx = int((xs[0] + xs[-1]) // 2)

        return top_y, neck_y, head_cx

    def _get_avatar_profile(self, image_path: str | Path, width: int, height: int) -> Dict:
        """Find or estimate facial coordinates for the given avatar image."""
        filename = Path(image_path).stem.lower()
        for key, data in _PRESET_FACE_DATA.items():
            if key in filename:
                return data

        # Tự dò vị trí đầu trên ảnh thật. Công thức cố định theo chiều cao ảnh chỉ đúng
        # với ảnh chân dung cắt sát; với ảnh toàn thân nó đẩy miệng xuống tận ngực.
        try:
            with Image.open(str(image_path)) as im:
                arr = np.array(im.convert("RGBA"))
            box = self._detect_head_box(arr)
        except Exception as e:  # noqa: BLE001
            logger.debug("Không dò được khuôn mặt trên {}: {}", image_path, e)
            box = None

        if box:
            top_y, neck_y, head_cx = box
            head_h = neck_y - top_y
            # Tỉ lệ nhân trắc: mắt ~0.45, miệng ~0.72 tính từ đỉnh đầu xuống cằm.
            eye_y = int(top_y + head_h * 0.45)
            mouth_y = int(top_y + head_h * 0.72)
            eye_spacing = max(4, int(head_h * 0.20))
            logger.debug(
                "Dò khuôn mặt {}: đỉnh đầu y={}, cổ y={}, mắt y={}, miệng y={}",
                Path(image_path).name, top_y, neck_y, eye_y, mouth_y,
            )
            return {
                "mouth": (head_cx, mouth_y),
                "mouth_w": max(6, int(head_h * 0.20)),
                "eyes": [(head_cx - eye_spacing, eye_y), (head_cx + eye_spacing, eye_y)],
                "eye_size": (max(3, int(head_h * 0.12)), max(2, int(head_h * 0.05))),
                "max_drop": max(2, int(head_h * 0.09)),
            }

        # Phương án cuối: ước lượng theo tỉ lệ ảnh chân dung cắt sát.
        logger.debug("Dùng ước lượng tỉ lệ cho {} (không tách được nền)", Path(image_path).name)
        cx = width // 2
        mouth_y = int(height * 0.30)
        eye_y = int(height * 0.23)
        eye_spacing = int(width * 0.08)

        return {
            "mouth": (cx, mouth_y),
            "mouth_w": int(width * 0.055),
            "eyes": [(cx - eye_spacing, eye_y), (cx + eye_spacing, eye_y)],
            "eye_size": (int(width * 0.032), int(height * 0.01)),
            "max_drop": int(height * 0.013),
        }

    def _apply_blink(
        self,
        frame: np.ndarray,
        eyes: List[Tuple[int, int]],
        eye_size: Tuple[int, int],
        blink_factor: float,
    ) -> np.ndarray:
        """Draw natural eyelid closure over the eye sockets."""
        if blink_factor <= 0.01:
            return frame

        ew, eh = eye_size
        h, w, _ = frame.shape
        out = frame.copy()

        for ex, ey in eyes:
            x1 = max(0, ex - ew // 2)
            x2 = min(w, ex + ew // 2)
            y1 = max(0, ey - eh // 2)
            y2 = min(h, ey + eh // 2)

            # Sample skin color from directly above the eye
            skin_y1 = max(0, y1 - eh)
            skin_y2 = y1
            if skin_y2 <= skin_y1 or y2 <= y1 or x2 <= x1:
                continue

            eyelid = out[skin_y1:skin_y2, x1:x2].copy()
            if eyelid.shape[0] != (y2 - y1):
                # Resize to match eye target height
                from PIL import Image as PImage
                eyelid_img = PImage.fromarray(eyelid).resize((x2 - x1, y2 - y1))
                eyelid = np.array(eyelid_img)

            # Eyelash line at the seam
            eyelid[-2:, :] = (eyelid[-2:, :] * 0.35).astype(np.uint8)

            # Blend eyelid onto eye socket based on blink closure amount
            alpha = min(1.0, max(0.0, blink_factor))
            out[y1:y2, x1:x2] = (
                eyelid * alpha + out[y1:y2, x1:x2] * (1.0 - alpha)
            ).astype(np.uint8)

        return out

    def _apply_mouth_opening(
        self,
        frame: np.ndarray,
        mouth_center: Tuple[int, int],
        mouth_w: int,
        max_drop: int,
        open_amount: float,
    ) -> np.ndarray:
        """Morph the lower lip and chin downwards to open the mouth in sync with voice."""
        if open_amount <= 0.04:
            return frame

        h, w, _ = frame.shape
        mx, my = mouth_center
        drop = int(open_amount * max_drop)
        if drop < 1:
            return frame

        out = frame.copy()
        patch_y1 = my
        patch_y2 = min(h, my + int(max_drop * 4.5))
        patch_x1 = max(0, mx - mouth_w)
        patch_x2 = min(w, mx + mouth_w)

        if patch_y2 <= patch_y1 or patch_x2 <= patch_x1:
            return frame

        chin_patch = frame[patch_y1:patch_y2, patch_x1:patch_x2].copy()

        # 1. Fill inner oral cavity gap
        gap_y1 = my
        gap_y2 = min(h, my + drop + 2)
        gap_h = gap_y2 - gap_y1
        gap_w = patch_x2 - patch_x1

        cavity = np.zeros((gap_h, gap_w, 3), dtype=np.uint8)
        # Deep mouth shading (maroon / dark red inside)
        cavity[:, :, 0] = 52
        cavity[:, :, 1] = 16
        cavity[:, :, 2] = 20

        # Subtle upper teeth highlight (warm white in top 3px)
        if drop >= 4 and gap_h >= 3:
            teeth_w = int(mouth_w * 0.55)
            tx1 = max(0, gap_w // 2 - teeth_w)
            tx2 = min(gap_w, gap_w // 2 + teeth_w)
            cavity[:3, tx1:tx2, 0] = 238
            cavity[:3, tx1:tx2, 1] = 232
            cavity[:3, tx1:tx2, 2] = 226

        out[gap_y1:gap_y2, patch_x1:patch_x2] = cavity

        # 2. Overlay displaced lower lip & chin patch with feathered bottom blend
        dest_y1 = my + drop
        dest_y2 = min(h, dest_y1 + (patch_y2 - patch_y1))
        src_h = dest_y2 - dest_y1

        alpha = np.ones((src_h, gap_w, 1), dtype=float)
        feather_len = max(2, int(src_h * 0.3))
        for fy in range(feather_len):
            alpha[-fy - 1, :, 0] = fy / float(feather_len)

        out[dest_y1:dest_y2, patch_x1:patch_x2] = (
            chin_patch[:src_h] * alpha
            + frame[dest_y1:dest_y2, patch_x1:patch_x2] * (1.0 - alpha)
        ).astype(np.uint8)

        return out

    def generate_idle_loop(
        self,
        image_path: str | Path,
        output_mp4: str | Path,
        duration: float = 6.0,
        fps: int = 25,
    ) -> str:
        """Create a seamless, living presenter video loop with natural breathing, sway, and blinking.

        Perfect for 24/7 background live streaming overlay on OBS.
        """
        img_p = Path(image_path)
        out_p = Path(output_mp4)
        out_p.parent.mkdir(parents=True, exist_ok=True)

        pil_img = Image.open(str(img_p)).convert("RGB")
        w, h = pil_img.size
        base_frame = np.array(pil_img)
        profile = self._get_avatar_profile(img_p, w, h)

        total_frames = int(round(duration * fps))

        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-s", f"{w}x{h}",
            "-pix_fmt", "rgb24",
            "-r", str(fps),
            "-i", "-",
            "-c:v", "libx264",
            "-preset", "faster",
            "-pix_fmt", "yuv420p",
            str(out_p),
        ]

        logger.info("Đang render video loop sống động cho người mẫu AI: {} ({} frames)", img_p.name, total_frames)
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        try:
            for i in range(total_frames):
                frame = base_frame.copy()

                # 1. Natural breathing: exact 2 full cycles in duration (seamless loop)
                # 3.0s per breath cycle
                breathe_y = int(round(6.0 * np.sin(2 * np.pi * 2.0 * i / float(total_frames))))

                # 2. Gentle body sway: 1 full cycle in duration (seamless loop)
                sway_x = int(round(8.0 * np.sin(2 * np.pi * 1.0 * i / float(total_frames))))

                if breathe_y != 0:
                    frame = np.roll(frame, breathe_y, axis=0)
                if sway_x != 0:
                    frame = np.roll(frame, sway_x, axis=1)

                # 3. Scheduled blinks (at 2.0s and 4.5s)
                t_sec = i / float(fps)
                blink_closure = 0.0
                for blink_t in [2.0, 4.5]:
                    dt = abs(t_sec - blink_t)
                    if dt < 0.10:  # 0.20s total blink window
                        blink_closure = 1.0 - (dt / 0.10)

                if blink_closure > 0.05:
                    frame = self._apply_blink(
                        frame,
                        profile["eyes"],
                        profile["eye_size"],
                        blink_closure,
                    )

                proc.stdin.write(frame.tobytes())

            proc.stdin.close()
            proc.wait()
            logger.info("Đã tạo video loop cử động thành công: {}", out_p)
            return str(out_p)

        except Exception as e:
            if proc and proc.stdin:
                proc.stdin.close()
            logger.error("Lỗi khi render video loop người mẫu: {}", e)
            raise

    def generate_talking_video(
        self,
        image_path: str | Path,
        audio_path: str | Path,
        output_mp4: str | Path,
        fps: int = 25,
    ) -> str:
        """Generate a full talking presenter video with speech lip-sync, nodding, and natural presence."""
        img_p = Path(image_path)
        aud_p = Path(audio_path)
        out_p = Path(output_mp4)
        out_p.parent.mkdir(parents=True, exist_ok=True)

        if not aud_p.exists():
            raise FileNotFoundError(f"Tệp âm thanh không tồn tại: {aud_p}")

        pil_img = Image.open(str(img_p)).convert("RGB")
        w, h = pil_img.size
        base_frame = np.array(pil_img)
        profile = self._get_avatar_profile(img_p, w, h)

        # 1. Convert audio to raw 16kHz mono PCM for fast, precise volume extraction
        temp_pcm = out_p.parent / f"temp_{out_p.stem}.pcm"
        cmd_pcm = [
            self.ffmpeg_bin,
            "-y",
            "-i", str(aud_p),
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

        # 2. Compute speech energy per frame
        energies = []
        for i in range(num_frames):
            chunk = pcm[i * samples_per_frame : (i + 1) * samples_per_frame]
            rms = np.sqrt(np.mean(chunk.astype(float) ** 2)) if len(chunk) else 0.0
            energies.append(rms)

        energies = np.array(energies)
        max_e = np.percentile(energies, 95) if len(energies) else 1.0
        max_e = max(max_e, 500.0)  # avoid division by zero on silent audio
        norm_e = np.clip(energies / max_e, 0.0, 1.0)

        # Smooth with attack / decay for organic mouth movement
        smoothed = np.zeros_like(norm_e)
        for i in range(len(norm_e)):
            if i == 0:
                smoothed[i] = norm_e[i]
            else:
                if norm_e[i] > smoothed[i - 1]:
                    smoothed[i] = 0.55 * smoothed[i - 1] + 0.45 * norm_e[i]
                else:
                    smoothed[i] = 0.65 * smoothed[i - 1] + 0.35 * norm_e[i]

        logger.info(
            "Đang render video Người Mẫu AI nói chuyện: {} ({} giây, {} frames)",
            img_p.name,
            f"{num_frames / fps:.1f}",
            num_frames,
        )

        cmd_ff = [
            self.ffmpeg_bin,
            "-y",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-s", f"{w}x{h}",
            "-pix_fmt", "rgb24",
            "-r", str(fps),
            "-i", "-",
            "-i", str(aud_p),
            "-c:v", "libx264",
            "-preset", "faster",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            str(out_p),
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
                frame = base_frame.copy()

                # 1. Dynamic Breathing, Body Sway & Speech Gesturing
                sway = int(round(7.0 * np.sin(2 * np.pi * i / 75.0)))
                breathe = int(round(4.5 * np.sin(2 * np.pi * i / 50.0)))
                # Head nod on spoken emphasis (expressive gesture)
                nod = int(round(e * 10.0))

                dy = breathe + nod
                if dy != 0:
                    frame = np.roll(frame, dy, axis=0)
                if sway != 0:
                    frame = np.roll(frame, sway, axis=1)

                # 2. Periodic natural eye blink (every ~3.2s)
                blink_closure = 0.0
                cycle_pos = (i % int(fps * 3.2)) / float(fps)
                if cycle_pos < 0.18:
                    blink_closure = 1.0 - abs(cycle_pos - 0.09) / 0.09

                if blink_closure > 0.05:
                    frame = self._apply_blink(
                        frame,
                        profile["eyes"],
                        profile["eye_size"],
                        blink_closure,
                    )

                # 3. Voice-driven mouth opening (Lip-Sync with teeth & inner oral depth)
                if e > 0.05:
                    frame = self._apply_mouth_opening(
                        frame,
                        profile["mouth"],
                        profile["mouth_w"],
                        int(profile["max_drop"] * 1.4),
                        e,
                    )

                proc.stdin.write(frame.tobytes())

            proc.stdin.close()
            proc.wait()
            logger.info("Đã tạo video Người Mẫu AI nói chuyện thành công: {}", out_p)
            return str(out_p)

        except Exception as e:
            if proc and proc.stdin:
                proc.stdin.close()
            logger.error("Lỗi khi render video Người Mẫu AI nói chuyện: {}", e)
            raise
