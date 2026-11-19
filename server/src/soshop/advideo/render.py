"""Render the host: LivePortrait on every frame, then breathing and sway.

Output is an intermediate, nearly lossless 720x1280 MP4 without audio. The
edit reads it back, so the slow part (about 1.2 s per frame on a laptop iGPU)
does not have to be redone to change captions or framing.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

from soshop.advideo import media
from soshop.advideo.director import DirectedFrame
from soshop.agent.lipsync.build import cover, load_image
from soshop.agent.lipsync.liveportrait import LivePortrait
from soshop.agent.lipsync.models import ensure_liveportrait, ensure_modnet
from soshop.agent.lipsync.motion import MotionFrame
from soshop.agent.lipsync.performance import FaceAnchor, Performer

logger = logging.getLogger("soshop.advideo")

WIDTH = 720
HEIGHT = 1280
_CRF = 12

ImageArray = npt.NDArray[np.uint8]
Progress = Callable[[float], None]


@dataclass(frozen=True)
class RenderedHost:
    path: Path
    width: int
    height: int
    fps: int
    frames: int
    face: FaceAnchor


def _matte(models_dir: Path, frame: ImageArray) -> npt.NDArray[np.float32] | None:
    from soshop.agent.lipsync.matte import person_matte

    try:
        ensure_modnet(models_dir)
        return person_matte(models_dir, frame)
    except Exception:
        logger.warning("no person matte; body motion uses a geometric mask", exc_info=True)
        return None


def render_host(
    image: str,
    frames: Sequence[DirectedFrame],
    out_path: Path,
    models_dir: Path,
    *,
    fps: int,
    width: int = WIDTH,
    height: int = HEIGHT,
    progress: Progress | None = None,
) -> RenderedHost:
    """Render ``frames`` of the portrait at ``image`` to ``out_path``."""
    ensure_liveportrait(models_dir)
    frame0 = cover(load_image(image), width, height)
    portrait = LivePortrait(models_dir)
    source = portrait.prepare(frame0)
    face = portrait.faces.detect(frame0)
    if face is None:
        raise ValueError("no face found in the portrait")
    x0, y0, x1, y1 = (float(v) for v in face.box)
    anchor = FaceAnchor(cx=(x0 + x1) / 2, cy=(y0 + y1) / 2, width=x1 - x0, height=y1 - y0)
    body = Performer(width, height, fps, anchor, _matte(models_dir, frame0), camera=False)

    encoder, sink = media.writer(
        [*media.raw_video_input(width, height, fps), *media.h264(_CRF), str(out_path)]
    )
    try:
        for index, directed in enumerate(frames):
            m = directed.motion
            exp = m.exp + portrait.lip_offset(source, directed.lip)
            image_out = portrait.render(
                source, MotionFrame(m.pitch, m.yaw, m.roll, m.tx, m.ty, exp)
            )
            body.step(directed.talking)
            sink.write(body.warp(image_out).tobytes())
            if progress is not None and index % 5 == 0:
                progress((index + 1) / len(frames))
    finally:
        sink.close()
        code = encoder.wait()
    if code != 0:
        raise RuntimeError(f"ffmpeg failed to encode the host video (exit {code})")
    return RenderedHost(out_path, width, height, fps, len(frames), anchor)
