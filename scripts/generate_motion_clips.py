"""Generate high-fidelity motion video clips for AI livestream presenter."""

from __future__ import annotations

import math
import os
import subprocess
import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
from pathlib import Path
from typing import Dict, List, Tuple

import cv2
import numpy as np
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AVATAR_DIR = PROJECT_ROOT / "storage" / "avatars"


def find_face_and_eyes(img: np.ndarray) -> Dict:
    """Locate head, eyes, and mouth in the portrait."""
    h, w, _ = img.shape
    # For mai_phuong (768 x 1376)
    # Head top is ~110-180, chin is ~360-400, eyes around y=240-270, mouth around y=310-340
    # Let's find green screen mask
    non_green = (img[:, :, 1] < 165) | (img[:, :, 2] > 100) | (img[:, :, 0] > 100)
    y_idx, x_idx = np.where(non_green)
    top_y = int(y_idx.min())
    bot_y = int(y_idx.max())
    mid_x = int(np.median(x_idx))

    # Face landmarks proportional to model height
    head_h = int((bot_y - top_y) * 0.18)
    eye_y = top_y + int(head_h * 0.58)
    mouth_y = top_y + int(head_h * 0.88)
    eye_spacing = int(w * 0.048)

    return {
        "top_y": top_y,
        "bot_y": bot_y,
        "mid_x": mid_x,
        "eye_y": eye_y,
        "mouth_y": mouth_y,
        "eyes": [(mid_x - eye_spacing, eye_y), (mid_x + eye_spacing, eye_y)],
        "eye_w": int(w * 0.038),
        "eye_h": int(h * 0.012),
        "mouth_w": int(w * 0.065),
    }


def apply_natural_blink(
    frame: np.ndarray,
    eyes: List[Tuple[int, int]],
    eye_w: int,
    eye_h: int,
    closure: float,
) -> np.ndarray:
    """Draw smooth biological eyelid closure with natural skin color and eyelashes."""
    if closure <= 0.02:
        return frame

    out = frame.copy()
    h, w, _ = frame.shape
    alpha = min(1.0, max(0.0, closure))

    for ex, ey in eyes:
        x1 = max(0, ex - eye_w // 2)
        x2 = min(w, ex + eye_w // 2)
        y1 = max(0, ey - eye_h // 2)
        y2 = min(h, ey + eye_h // 2)

        if y2 <= y1 or x2 <= x1:
            continue

        # Sample skin patch from right above the brow/eyelid
        skin_y1 = max(0, y1 - eye_h - 4)
        skin_y2 = max(0, y1 - 4)
        if skin_y2 <= skin_y1:
            continue

        skin = out[skin_y1:skin_y2, x1:x2]
        if skin.shape[0] == 0 or skin.shape[1] == 0:
            continue

        eyelid = cv2.resize(skin, (x2 - x1, y2 - y1), interpolation=cv2.INTER_LINEAR)
        # Eyelash dark seam
        seam_h = max(1, int(eye_h * 0.25))
        eyelid[-seam_h:, :] = (eyelid[-seam_h:, :] * 0.35).astype(np.uint8)

        # Blend
        out[y1:y2, x1:x2] = (eyelid * alpha + out[y1:y2, x1:x2] * (1.0 - alpha)).astype(np.uint8)

    return out


def generate_kinematic_clip(
    image_path: Path,
    output_path: Path,
    mode: str = "idle",
    duration: float = 6.0,
    fps: int = 30,
) -> str:
    """Generate seamless 1080x1920 MP4 video clip with natural human kinematic motion."""
    base_bgr = cv2.imread(str(image_path))
    if base_bgr is None:
        raise FileNotFoundError(f"Cannot read image: {image_path}")

    # Standardize to 1080x1920 vertical canvas
    target_w, target_h = 1080, 1920
    bh, bw, _ = base_bgr.shape
    scale = min(target_w / bw, target_h / bh)
    new_w = int(bw * scale)
    new_h = int(bh * scale)
    resized = cv2.resize(base_bgr, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)

    # Place on 1080x1920 canvas with green background
    green_bg = np.zeros((target_h, target_w, 3), dtype=np.uint8)
    green_bg[:, :] = [34, 190, 67]  # Standard green screen BGR
    pad_x = (target_w - new_w) // 2
    pad_y = target_h - new_h
    green_bg[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized

    frame_base = green_bg
    fh, fw, _ = frame_base.shape
    landmarks = find_face_and_eyes(frame_base)

    total_frames = int(duration * fps)
    temp_raw = output_path.with_suffix(".temp.mp4")

    # FFmpeg pipe
    cmd = [
        "ffmpeg",
        "-y",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-s", f"{fw}x{fh}",
        "-pix_fmt", "bgr24",
        "-r", str(fps),
        "-i", "-",
        "-c:v", "libx264",
        "-preset", "faster",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(temp_raw),
    ]

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        for i in range(total_frames):
            phase = i / float(total_frames)
            t_sec = i / float(fps)
            frame = frame_base.copy()

            # 1. Breathing motion: 2 full cycles in duration (seamless loop)
            # Upper body breathes, feet stay anchored
            breathe_cycle = math.sin(2 * math.pi * 2.0 * phase)
            breathe_dy = -breathe_cycle * 5.0  # -5px up, +5px down

            # 2. Gentle sway / weight shift: 1 full cycle in duration (seamless loop)
            sway_dx = math.sin(2 * math.pi * 1.0 * phase) * 6.0
            tilt_deg = math.sin(2 * math.pi * 1.0 * phase) * 0.6

            # Specific motion per mode
            if mode == "talk":
                # Active head nod with speech rhythm (4 nods per loop)
                nod_dy = math.sin(2 * math.pi * 4.0 * phase) * 3.5
                breathe_dy += nod_dy
                # Active hand gesture subtle pulse
                hand_pulse = math.sin(2 * math.pi * 3.0 * phase) * 4.0
                sway_dx += hand_pulse * 0.5
            elif mode == "point":
                # Pointing accent pulse (emphasis on product)
                point_pulse = math.sin(2 * math.pi * 2.0 * phase) * 4.0
                sway_dx += point_pulse * 0.8
                tilt_deg -= 0.5  # slight tilt towards product

            # Apply smooth affine warp (anchored at bottom feet y=fh-50)
            center_x = fw / 2.0
            anchor_y = fh - 80.0
            M = cv2.getRotationMatrix2D((center_x, anchor_y), tilt_deg, 1.0)
            M[0, 2] += sway_dx
            M[1, 2] += breathe_dy

            warped = cv2.warpAffine(
                frame,
                M,
                (fw, fh),
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=(34, 190, 67),
            )

            # 3. Scheduled Blinks (t=2.2s and t=4.8s)
            blink_closure = 0.0
            for blink_t in [2.2, 4.8]:
                dt = abs(t_sec - blink_t)
                if dt < 0.12:  # 0.24s total blink window
                    blink_closure = math.cos((dt / 0.12) * (math.pi / 2.0))

            if blink_closure > 0.05:
                # Calculate transformed eye positions
                eyes = landmarks["eyes"]
                eye_w = landmarks["eye_w"]
                eye_h = landmarks["eye_h"]
                transformed_eyes = []
                for ex, ey in eyes:
                    pt = np.array([ex, ey, 1.0])
                    new_pt = M.dot(pt)
                    transformed_eyes.append((int(new_pt[0]), int(new_pt[1])))

                warped = apply_natural_blink(
                    warped,
                    transformed_eyes,
                    eye_w,
                    eye_h,
                    blink_closure,
                )

            proc.stdin.write(warped.tobytes())

        proc.stdin.close()
        proc.wait()

        # Replace target
        if output_path.exists():
            output_path.unlink()
        temp_raw.rename(output_path)
        print(f"✅ Generated {mode} video: {output_path.name} ({duration}s @ {fps}fps)")
        return str(output_path)

    except Exception as e:
        if proc and proc.stdin:
            proc.stdin.close()
        raise e


def build_all_avatar_motion_clips():
    """Build all 4 motion clips for Mai Phuong."""
    poses = {
        "idle": (AVATAR_DIR / "mai_phuong_idle.jpg", AVATAR_DIR / "mai_phuong_idle.mp4"),
        "talk": (AVATAR_DIR / "mai_phuong_talking.jpg", AVATAR_DIR / "mai_phuong_talk.mp4"),
        "point": (AVATAR_DIR / "mai_phuong_point.jpg", AVATAR_DIR / "mai_phuong_point.mp4"),
        "cta": (AVATAR_DIR / "mai_phuong_talking.jpg", AVATAR_DIR / "mai_phuong_cta.mp4"),
    }

    for mode, (img_p, out_p) in poses.items():
        if img_p.exists():
            generate_kinematic_clip(img_p, out_p, mode=mode, duration=6.0, fps=30)
        else:
            print(f"⚠️ Source pose missing: {img_p}")


if __name__ == "__main__":
    build_all_avatar_motion_clips()
