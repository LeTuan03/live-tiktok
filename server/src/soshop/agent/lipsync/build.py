"""Build a model's avatar assets from one portrait photo.

    python -m soshop.agent.lipsync.build --image <path or http url> \\
        --out <assets_dir> [--models <models_dir>] [--fps 25] [--seconds 12]

Renders the looping motion clip (LivePortrait, scripted motion) in two
behavior tracks, idle and talk, records where the face is in every frame, and
extracts a person matte for the realtime body motion. Progress is written to
``<assets_dir>/status.json`` as it goes; the API spawns this as a subprocess
and polls that file. Takes about 12 to 15 minutes on a laptop with an Intel
iGPU, almost all of it the roughly 570 LivePortrait frames. Model weights
(about 530 MB for this step) are downloaded into the models directory on
first use.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import cv2
import httpx
import numpy as np
import numpy.typing as npt

from soshop.agent.lipsync import align, assets
from soshop.agent.lipsync.liveportrait import LivePortrait
from soshop.agent.lipsync.matte import person_matte, save_matte
from soshop.agent.lipsync.models import ensure_liveportrait, ensure_modnet, ensure_wav2lip
from soshop.agent.lipsync.motion import IDLE, TALK, TRACK_NAMES, MotionScript
from soshop.agent.lipsync.wav2lip import FACE_SIZE

logger = logging.getLogger("soshop.agent.lipsync")

WIDTH = 540
HEIGHT = 960
DEFAULT_FPS = 25
DEFAULT_SECONDS = 12.0
DEFAULT_MODELS_DIR = Path("data/models")

_JPEG_QUALITY = 92
_MIN_FACE_PX = 90
# The lip-sync model was trained on face crops padded a little below the chin.
_CHIN_PAD = 0.04
_SMOOTH_WINDOW = 9

# Shares of the progress bar.
_P_MODELS = 0.12
_P_PREPARE = 0.16
_P_FRAMES = 0.97

ImageArray = npt.NDArray[np.uint8]


def _load_image(source: str) -> ImageArray:
    if source.startswith(("http://", "https://")):
        resp = httpx.get(source, follow_redirects=True, timeout=30.0)
        resp.raise_for_status()
        data = resp.content
    else:
        data = Path(source).read_bytes()
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("the portrait is not a readable image")
    return np.asarray(image, dtype=np.uint8)


def cover(image: ImageArray, width: int = WIDTH, height: int = HEIGHT) -> ImageArray:
    """Scale and crop to a ``width`` x ``height`` frame, biased toward the top."""
    scale = max(width / image.shape[1], height / image.shape[0])
    interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    resized = cv2.resize(
        image,
        (max(width, round(image.shape[1] * scale)), max(height, round(image.shape[0] * scale))),
        interpolation=interpolation,
    )
    left = (resized.shape[1] - width) // 2
    top = (resized.shape[0] - height) // 4
    return np.ascontiguousarray(resized[top : top + height, left : left + width], dtype=np.uint8)


def load_image(source: str) -> ImageArray:
    """Read a portrait from a local path or an http(s) URL."""
    return _load_image(source)


def _stable_boxes(raw: npt.NDArray[np.float32]) -> npt.NDArray[np.int32]:
    """Constant-size boxes whose centers are smoothed around the loop."""
    widths = raw[:, 2] - raw[:, 0]
    heights = raw[:, 3] - raw[:, 1]
    width = int(round(float(np.median(widths))))
    height = int(round(float(np.median(heights)) * (1 + _CHIN_PAD)))
    centers = np.stack([(raw[:, 0] + raw[:, 2]) / 2, raw[:, 1]], axis=1)  # x center, top
    pad = _SMOOTH_WINDOW // 2
    wrapped = np.concatenate([centers[-pad:], centers, centers[:pad]])
    kernel = np.ones(_SMOOTH_WINDOW) / _SMOOTH_WINDOW
    smooth = np.stack([np.convolve(wrapped[:, k], kernel, mode="valid") for k in range(2)], axis=1)
    x0 = np.clip(np.round(smooth[:, 0] - width / 2), 0, WIDTH - width)
    y0 = np.clip(np.round(smooth[:, 1]), 0, HEIGHT - height)
    return np.stack([x0, y0, x0 + width, y0 + height], axis=1).astype(np.int32)


def _write_matte(models_dir: Path, out_dir: Path, frame: ImageArray) -> None:
    """Save the person matte. Optional: without it the body motion falls back
    to a rough geometric body region, so a failure here only logs."""
    try:
        ensure_modnet(models_dir)
        save_matte(out_dir / assets.MATTE_FILE, person_matte(models_dir, frame))
    except Exception:
        logger.warning(
            "person matte unavailable; body motion will use a geometric mask", exc_info=True
        )


def _render_plan(script: MotionScript) -> list[tuple[int, int]]:
    """(timeline index, track) pairs to render. Frames both tracks share go once."""
    plan: list[tuple[int, int]] = []
    for index in range(script.total):
        plan.append((index, IDLE))
        if not script.shared(index):
            plan.append((index, TALK))
    return plan


def build(
    image_source: str,
    out_dir: Path,
    models_dir: Path,
    *,
    fps: int = DEFAULT_FPS,
    seconds: float = DEFAULT_SECONDS,
) -> None:
    """Build the assets, raising on failure. Status is the caller's concern."""
    total = max(fps, round(fps * seconds))
    assets.write_status(out_dir, "processing", 0.0)

    ensure_liveportrait(
        models_dir, lambda p: assets.write_status(out_dir, "processing", p * _P_MODELS)
    )
    ensure_wav2lip(models_dir)  # runtime needs it; fetch now so going live is instant
    assets.write_status(out_dir, "processing", _P_MODELS)

    frame0 = cover(_load_image(image_source))
    portrait = LivePortrait(models_dir)
    source = portrait.prepare(frame0)
    face = portrait.faces.detect(frame0)
    if face is None or (face.box[2] - face.box[0]) < _MIN_FACE_PX:
        raise ValueError("the face is too small; use a closer portrait (head and shoulders)")
    _write_matte(models_dir, out_dir, frame0)
    assets.write_status(out_dir, "processing", _P_PREPARE)

    script = MotionScript(total, fps)
    plan = _render_plan(script)
    n_tracks = len(TRACK_NAMES)
    tracks = np.zeros((n_tracks, total), dtype=np.int32)
    landmarks: list[list[npt.NDArray[np.float32] | None]] = [[None] * total for _ in TRACK_NAMES]
    boxes: list[npt.NDArray[np.float32]] = []
    offsets = [0]
    encode = [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY]
    frames_tmp = out_dir / (assets.FRAMES_FILE + ".tmp")
    with frames_tmp.open("wb") as sink:
        for stored, (index, track) in enumerate(plan):
            frame = portrait.render(source, script.frame(track, index))
            found = portrait.faces.detect(frame) or face
            ok, jpeg = cv2.imencode(".jpg", frame, encode)
            if not ok:
                raise RuntimeError("failed to encode a frame")
            sink.write(jpeg.tobytes())
            offsets.append(offsets[-1] + int(jpeg.size))
            marks = portrait.faces.landmarks(frame, found)
            tracks[track, index] = stored
            landmarks[track][index] = marks
            if track == IDLE:
                boxes.append(found.box)
                if script.shared(index):
                    tracks[TALK, index] = stored
                    landmarks[TALK][index] = marks
            if stored == 0:
                cv2.imwrite(str(out_dir / assets.PREVIEW_FILE), frame, encode)
            if stored % 5 == 0:
                share = _P_PREPARE + (_P_FRAMES - _P_PREPARE) * (stored + 1) / len(plan)
                assets.write_status(out_dir, "processing", share)
                logger.info("frame %d/%d", stored + 1, len(plan))

    per_track = [[m for m in marks if m is not None] for marks in landmarks]
    if any(len(marks) != total for marks in per_track):
        raise RuntimeError("a track is missing frames")
    eye_mouth = align.eye_mouth_distance([m for marks in per_track for m in marks])
    transforms = np.stack(
        [align.clip_transforms(marks, FACE_SIZE, eye_mouth) for marks in per_track]
    )
    portals = script.portals()
    if not portals.any():
        portals[0] = True  # the loop seam is always a slot boundary

    np.save(out_dir / assets.FACES_FILE, _stable_boxes(np.stack(boxes)))
    np.save(out_dir / assets.ALIGN_FILE, transforms.astype(np.float32))
    np.save(out_dir / assets.TRACKS_FILE, tracks)
    np.save(out_dir / assets.PORTALS_FILE, portals)
    np.save(out_dir / assets.INDEX_FILE, np.asarray(offsets, dtype=np.int64))
    frames_tmp.replace(out_dir / assets.FRAMES_FILE)
    meta = {
        "version": assets.FORMAT_VERSION,
        "width": WIDTH,
        "height": HEIGHT,
        "fps": fps,
        "frames": total,
        "tracks": list(TRACK_NAMES),
        "stored_frames": len(plan),
    }
    (out_dir / assets.META_FILE).write_text(json.dumps(meta), encoding="utf-8")
    assets.write_status(out_dir, "ready", 1.0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--image", required=True, help="portrait: local path or http(s) url")
    parser.add_argument("--out", required=True, type=Path, help="assets directory to write")
    parser.add_argument("--models", type=Path, default=DEFAULT_MODELS_DIR)
    parser.add_argument("--fps", type=int, default=DEFAULT_FPS)
    parser.add_argument("--seconds", type=float, default=DEFAULT_SECONDS)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    try:
        build(args.image, args.out, args.models, fps=args.fps, seconds=args.seconds)
    except Exception as exc:
        logger.exception("avatar build failed")
        assets.write_status(args.out, "failed", 0.0, str(exc) or type(exc).__name__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
