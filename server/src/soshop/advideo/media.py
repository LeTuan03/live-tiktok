"""ffmpeg processes for the ad video: decode audio, stream raw frames in and out."""

from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache
from typing import IO


@lru_cache(maxsize=1)
def ffmpeg() -> str:
    """Full path of the ffmpeg binary. Raises when it is not installed."""
    path = shutil.which("ffmpeg")
    if path is None:
        raise FileNotFoundError("ffmpeg is not on PATH; install it to render videos")
    return path


def run(args: list[str], data: bytes) -> bytes:
    """Run ffmpeg with ``data`` on stdin and return its stdout."""
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [ffmpeg(), "-loglevel", "error", *args], input=data, capture_output=True, check=True
    )
    return proc.stdout


def writer(args: list[str]) -> tuple[subprocess.Popen[bytes], IO[bytes]]:
    """Start ffmpeg reading from a pipe. Returns the process and its stdin."""
    proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        [ffmpeg(), "-y", "-loglevel", "error", *args], stdin=subprocess.PIPE
    )
    if proc.stdin is None:
        raise RuntimeError("ffmpeg has no stdin")
    return proc, proc.stdin


def reader(args: list[str]) -> tuple[subprocess.Popen[bytes], IO[bytes]]:
    """Start ffmpeg writing to a pipe. Returns the process and its stdout."""
    proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        [ffmpeg(), "-loglevel", "error", *args], stdout=subprocess.PIPE
    )
    if proc.stdout is None:
        raise RuntimeError("ffmpeg has no stdout")
    return proc, proc.stdout


def raw_video_input(width: int, height: int, fps: int) -> list[str]:
    """Arguments for raw BGR frames on stdin."""
    return [
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "pipe:0",
    ]


def h264(crf: int) -> list[str]:
    return ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p"]
