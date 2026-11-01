"""Video Stitcher & Audio Alignment Engine for Veo 3 Scenes.

Aligns Veo 3 video clips with Edge-TTS speech, burns in captions,
and stitches multi-scene videos with smooth audio ducking and transitions.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from typing import List, Optional

from loguru import logger

from app.config.settings import get_settings
from app.integrations.ffmpeg.ffprobe_service import FFProbeService


class VeoStitcher:
    """Stitches multi-scene clips, aligns speech audio, and produces finished showcase videos."""

    def __init__(self, ffmpeg_path: Optional[str] = None) -> None:
        settings = get_settings()
        self.ffmpeg_cmd = ffmpeg_path or settings.ffmpeg_path or "ffmpeg"
        self.ffprobe = FFProbeService()

    def get_duration(self, file_path: str | Path) -> float:
        """Get precise media duration in seconds via FFprobe."""
        try:
            d = self.ffprobe.get_duration(file_path)
            if d and d > 0:
                return float(d)
        except Exception:
            pass
        return 5.0

    async def align_scene_video_audio(
        self,
        video_path: str | Path,
        audio_path: str | Path,
        output_path: str | Path,
        caption_text: Optional[str] = None,
    ) -> str:
        """Synchronize video clip duration with TTS audio and add clean captions.

        Args:
            video_path: Path to Veo 3 generated MP4 clip.
            audio_path: Path to TTS spoken audio file.
            output_path: Target path for the synchronized scene clip.
            caption_text: Spoken sentence to burn into the bottom of the video.

        Returns:
            Path to synchronized MP4 clip.
        """
        video_p = Path(video_path)
        audio_p = Path(audio_path)
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)

        vid_dur = self.get_duration(video_p)
        aud_dur = self.get_duration(audio_p)
        target_dur = max(vid_dur, aud_dur + 0.2)

        logger.info(
            "Align scene: video_dur={:.2f}s, audio_dur={:.2f}s -> target={:.2f}s",
            vid_dur,
            aud_dur,
            target_dur,
        )

        # Build video filter
        vf_filters: list[str] = []

        # If audio is longer than video, clone last frame to fill the duration smoothly
        if aud_dur > vid_dur:
            pad_seconds = aud_dur - vid_dur + 0.25
            vf_filters.append(f"tpad=stop_mode=clone:stop_duration={pad_seconds:.2f}")

        # Ensure video is 1080x1920 vertical format
        vf_filters.append("scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black")

        # Optional caption overlay
        if caption_text:
            clean_sub = caption_text.replace("'", "").replace('"', "").replace(":", " ")
            # Wrap text if long
            if len(clean_sub) > 35:
                words = clean_sub.split()
                mid = len(words) // 2
                clean_sub = " ".join(words[:mid]) + "\\\n" + " ".join(words[mid:])

            font_opt = ""
            for font_cand in [Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/segoeui.ttf"), Path("C:/Windows/Fonts/tahoma.ttf")]:
                if font_cand.exists():
                    fpath = str(font_cand.resolve()).replace("\\", "/").replace(":", "\\:")
                    font_opt = f":fontfile='{fpath}'"
                    break

            # Draw stylish subtitle box
            vf_filters.append(
                f"drawtext=text='{clean_sub}'{font_opt}:fontcolor=white:fontsize=46:line_spacing=12:"
                "box=1:boxcolor=black@0.65:boxborderw=18:x=(w-text_w)/2:y=h-th-280"
            )

        vf_str = ",".join(vf_filters)

        cmd = [
            self.ffmpeg_cmd,
            "-hwaccel", "none",
            "-y",
            "-i", str(video_p),
            "-i", str(audio_p),
            "-filter_complex", f"[0:v]{vf_str}[v];[1:a]apad[a]",
            "-map", "[v]",
            "-map", "[a]",
            "-t", f"{target_dur:.2f}",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "21",
            "-c:a", "aac",
            "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            "-r", "30",
            str(out_p),
        ]

        logger.debug("Chạy FFmpeg align: {}", " ".join(cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            err_msg = stderr.decode("utf-8", errors="replace")
            logger.warning("FFmpeg align cảnh báo lỗi ({}): {}", proc.returncode, err_msg[-300:])
            # Fallback simple merge without complex filter if error
            await self._fallback_merge(video_p, audio_p, out_p, target_dur)

        return str(out_p)

    async def _fallback_merge(
        self, video_p: Path, audio_p: Path, out_p: Path, target_dur: float
    ) -> None:
        cmd = [
            self.ffmpeg_cmd,
            "-hwaccel", "none",
            "-y",
            "-i", str(video_p),
            "-i", str(audio_p),
            "-c:v", "copy",
            "-c:a", "aac",
            "-shortest",
            str(out_p),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    async def stitch_scenes(
        self,
        scene_paths: List[str | Path],
        output_path: str | Path,
        bgm_path: Optional[str | Path] = None,
        bgm_volume: float = 0.20,
    ) -> str:
        """Concatenate multiple scene clips into a single finished video file.

        Args:
            scene_paths: List of scene video file paths.
            output_path: Output path for the merged MP4.
            bgm_path: Optional background music audio file.
            bgm_volume: Volume ratio for BGM (0.0 to 1.0).

        Returns:
            Path to final showcase MP4.
        """
        valid_paths = [Path(p) for p in scene_paths if Path(p).exists() and Path(p).stat().st_size > 0]
        if not valid_paths:
            raise ValueError("Không có video phân cảnh nào hợp lệ để ghép nối.")

        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)

        # Build concat list file
        concat_file = out_p.parent / f"concat_{out_p.stem}.txt"
        with open(concat_file, "w", encoding="utf-8") as f:
            for vp in valid_paths:
                clean_path = str(vp.resolve()).replace("\\", "/")
                f.write(f"file '{clean_path}'\n")

        # If no BGM, simple demuxer concat
        if not bgm_path or not Path(bgm_path).exists():
            cmd = [
                self.ffmpeg_cmd,
                "-hwaccel", "none",
                "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_file),
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "20",
                "-c:a", "aac",
                "-b:a", "192k",
                "-pix_fmt", "yuv420p",
                "-r", "30",
                str(out_p),
            ]
        else:
            # Concat video + loop BGM with sidechain ducking or volume mix
            cmd = [
                self.ffmpeg_cmd,
                "-hwaccel", "none",
                "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_file),
                "-stream_loop", "-1",
                "-i", str(bgm_path),
                "-filter_complex",
                f"[1:a]volume={bgm_volume:.2f}[bgm];[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]",
                "-map", "0:v",
                "-map", "[aout]",
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "20",
                "-c:a", "aac",
                "-b:a", "192k",
                "-pix_fmt", "yuv420p",
                "-r", "30",
                str(out_p),
            ]

        logger.info("Bắt đầu ghép {} phân cảnh vào {}", len(valid_paths), out_p)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        # Clean concat file
        try:
            if concat_file.exists():
                concat_file.unlink()
        except Exception:
            pass

        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace")
            raise RuntimeError(f"Lỗi khi ghép video bằng FFmpeg: {err[-400:]}")

        logger.info("Đã ghép video hoàn tất: {} (size: {:.2f} MB)", out_p, out_p.stat().st_size / 1024 / 1024)
        return str(out_p)
