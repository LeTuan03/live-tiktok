"""One call from product facts to a finished MP4, with progress on disk.

``out_dir`` ends up holding:

* ``status.json``  ``{"status", "progress", "error"}`` (same contract as the
  avatar build, so the API can poll it the same way)
* ``script.json``  the script that was spoken (edit it and pass it back with
  ``--script`` to re-voice)
* ``host.mp4``     the rendered host without audio, reused by ``reuse_host``
  when only the edit changes
* ``video.mp4``    the finished 1080x1920 video
* ``thumb.jpg``    a frame for previews
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from soshop.advideo import director, edit, render, voice
from soshop.advideo.script import AdScript, ProductBrief, ScriptError, fallback_script, write_script
from soshop.agent.lipsync.assets import write_status

logger = logging.getLogger("soshop.advideo")

SCRIPT_FILE = "script.json"
HOST_FILE = "host.mp4"
HOST_META_FILE = "host.json"
VIDEO_FILE = "video.mp4"
THUMB_FILE = "thumb.jpg"

# Shares of the progress bar.
_P_SCRIPT = 0.03
_P_VOICE = 0.08
_P_RENDER = 0.93


@dataclass(frozen=True)
class VideoRequest:
    portrait: str
    """The model's photo: local path or http(s) URL."""
    product: ProductBrief
    voice: str = "google-vi"
    language: str = "vi"
    script: AdScript | None = None
    music: Path | None = None
    fps: int = director.FPS
    reuse_host: bool = False
    """Skip the slow render when ``host.mp4`` from an identical script exists."""


async def _script(request: VideoRequest, out_dir: Path) -> AdScript:
    script = request.script
    if script is None:
        try:
            script = await write_script(request.product, request.language)
        except ScriptError as exc:
            logger.warning("LLM script failed (%s); using the template script", exc)
            script = fallback_script(request.product, request.language)
    (out_dir / SCRIPT_FILE).write_text(
        json.dumps(script.to_json(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return script


def _cached_host(out_dir: Path, script: AdScript, frames: int) -> render.RenderedHost | None:
    meta_path = out_dir / HOST_META_FILE
    if not (out_dir / HOST_FILE).is_file() or not meta_path.is_file():
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("script") != script.to_json() or meta.get("frames") != frames:
        return None
    face = render.FaceAnchor(**meta["face"])
    return render.RenderedHost(
        out_dir / HOST_FILE, meta["width"], meta["height"], meta["fps"], frames, face
    )


def _save_host_meta(out_dir: Path, host: render.RenderedHost, script: AdScript) -> None:
    meta = {
        "script": script.to_json(),
        "frames": host.frames,
        "width": host.width,
        "height": host.height,
        "fps": host.fps,
        "face": {
            "cx": host.face.cx,
            "cy": host.face.cy,
            "width": host.face.width,
            "height": host.face.height,
        },
    }
    (out_dir / HOST_META_FILE).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


async def make_video(request: VideoRequest, out_dir: Path, models_dir: Path) -> Path:
    """Run the whole pipeline. Raises on failure; status is the caller's concern."""
    out_dir.mkdir(parents=True, exist_ok=True)
    write_status(out_dir, "processing", 0.0)
    script = await _script(request, out_dir)
    write_status(out_dir, "processing", _P_SCRIPT)

    track = await voice.speak(script, voice.keyless_synthesizer(request.voice, request.language))
    frames = director.direct(track, script, request.fps)
    write_status(out_dir, "processing", _P_VOICE)
    logger.info("voice %.1f s, %d frames to render", track.duration, len(frames))

    host = _cached_host(out_dir, script, len(frames)) if request.reuse_host else None
    if host is None:
        host = render.render_host(
            request.portrait,
            frames,
            out_dir / HOST_FILE,
            models_dir,
            fps=request.fps,
            progress=lambda p: write_status(
                out_dir, "processing", _P_VOICE + (_P_RENDER - _P_VOICE) * p
            ),
        )
        _save_host_meta(out_dir, host, script)

    video = edit.compose(
        host,
        track,
        script,
        request.product,
        out_dir / VIDEO_FILE,
        music=request.music,
        thumbnail=out_dir / THUMB_FILE,
        progress=lambda p: write_status(out_dir, "processing", _P_RENDER + (1 - _P_RENDER) * p),
    )
    write_status(out_dir, "ready", 1.0)
    return video
