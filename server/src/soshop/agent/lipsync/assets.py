"""On-disk format of one model's avatar assets, shared by build and runtime.

``assets_dir`` holds:

* ``status.json``  build state the API polls:
  ``{"status": "processing" | "ready" | "failed", "progress": 0..1, "error": str | null}``
* ``meta.json``    frame size, fps, frame count, format version
* ``frames.bin``   the motion clip as concatenated JPEGs (full frames)
* ``frames.npy``   int64 byte offsets into ``frames.bin``, one per frame plus the end
* ``faces.npy``    int32 ``(frames, 4)`` face box per frame, the region the
  realtime lip sync repaints, as ``x0, y0, x1, y1`` with a constant size
* ``align.npy``    float32 ``(tracks, frames, 2, 3)`` affine per track and frame
  mapping the upright 96x96 face the lip-sync model works on to frame pixels.
  Version 1 clips store ``(frames, 2, 3)``; clips built before alignment
  existed have none and fall back to the face box
* ``tracks.npy``   int32 ``(tracks, frames)``: which stored frame shows each
  timeline position of each behavior track (0 idle, 1 talk). Version 2 only.
  Frames both tracks share are stored once
* ``portals.npy``  bool ``(frames,)``: timeline positions where all tracks show
  the same pose, so playback may switch track there. Version 2 only
* ``matte.png``    8-bit person matte of the first frame. Optional
* ``preview.jpg``  first frame, for UI thumbnails

Version 1 is a single looping track and still loads.

JPEG keeps a 12 s clip near 20 MB instead of 450 MB of raw frames, and one
decode per displayed frame costs a few milliseconds.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

FORMAT_VERSION = 2
SUPPORTED_VERSIONS = (1, 2)

STATUS_FILE = "status.json"
META_FILE = "meta.json"
FRAMES_FILE = "frames.bin"
INDEX_FILE = "frames.npy"
FACES_FILE = "faces.npy"
ALIGN_FILE = "align.npy"
TRACKS_FILE = "tracks.npy"
PORTALS_FILE = "portals.npy"
MATTE_FILE = "matte.png"
PREVIEW_FILE = "preview.jpg"

_STATUS_RETRY_S = 0.05
_PROGRESS_ATTEMPTS = 20
_FINAL_STATUS_ATTEMPTS = 400

ImageArray = npt.NDArray[np.uint8]


def write_status(assets_dir: Path, status: str, progress: float, error: str | None = None) -> None:
    """Atomically replace ``status.json`` so a poller never reads a torn file."""
    assets_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": status,
        "progress": round(min(max(progress, 0.0), 1.0), 3),
        "error": error,
    }
    partial = assets_dir / (STATUS_FILE + ".tmp")
    partial.write_text(json.dumps(payload), encoding="utf-8")
    # On Windows the replace fails with PermissionError while another process
    # has status.json open, and the API polls it every couple of seconds during
    # a build. Retry briefly. A progress tick that still cannot land is dropped
    # (the next one will); a final status must land, so it waits much longer.
    attempts = _FINAL_STATUS_ATTEMPTS if status != "processing" else _PROGRESS_ATTEMPTS
    for attempt in range(attempts):
        try:
            partial.replace(assets_dir / STATUS_FILE)
            return
        except PermissionError:
            if attempt == attempts - 1:
                if status != "processing":
                    raise
                return
            time.sleep(_STATUS_RETRY_S)


def read_status(assets_dir: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((assets_dir / STATUS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def assets_ready(assets_dir: Path) -> bool:
    """True when a finished, current-format build is present in ``assets_dir``."""
    status = read_status(assets_dir)
    if status is None or status.get("status") != "ready":
        return False
    if not all((assets_dir / name).is_file() for name in (FRAMES_FILE, INDEX_FILE, FACES_FILE)):
        return False
    try:
        meta = json.loads((assets_dir / META_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(meta, dict) and meta.get("version") in SUPPORTED_VERSIONS


@dataclass(frozen=True)
class MotionClip:
    """A loaded motion clip. Frames decode lazily from the in-memory JPEGs.

    Frame lookups take a timeline ``index`` and a behavior ``track``; a
    version 1 clip has one track, and asking it for another returns track 0.
    """

    width: int
    height: int
    fps: int
    faces: npt.NDArray[np.int32]
    _data: bytes
    _index: npt.NDArray[np.int64]
    tracks: npt.NDArray[np.int32]
    """``(tracks, frames)`` stored-frame id per track and timeline position."""
    portals: npt.NDArray[np.bool_]
    align: npt.NDArray[np.float32] | None = None
    """``(tracks, frames, 2, 3)`` or ``None`` for clips without alignment."""
    matte_path: Path | None = None

    @classmethod
    def load(cls, assets_dir: Path) -> MotionClip:
        meta = json.loads((assets_dir / META_FILE).read_text(encoding="utf-8"))
        index = np.load(assets_dir / INDEX_FILE)
        stored = int(index.shape[0]) - 1
        tracks_path = assets_dir / TRACKS_FILE
        if tracks_path.is_file():
            tracks = np.load(tracks_path).astype(np.int32)
        else:
            tracks = np.arange(stored, dtype=np.int32)[None]
        portals_path = assets_dir / PORTALS_FILE
        if portals_path.is_file():
            portals = np.load(portals_path).astype(bool)
        else:
            portals = np.ones(tracks.shape[1], dtype=bool)
        align = None
        align_path = assets_dir / ALIGN_FILE
        if align_path.is_file():
            align = np.load(align_path).astype(np.float32)
            if align.ndim == 3:
                align = align[None]
        matte_path = assets_dir / MATTE_FILE
        return cls(
            width=int(meta["width"]),
            height=int(meta["height"]),
            fps=int(meta["fps"]),
            faces=np.load(assets_dir / FACES_FILE),
            _data=(assets_dir / FRAMES_FILE).read_bytes(),
            _index=index,
            tracks=tracks,
            portals=portals,
            align=align,
            matte_path=matte_path if matte_path.is_file() else None,
        )

    def __len__(self) -> int:
        """Timeline length in frames (the loop length of every track)."""
        return int(self.tracks.shape[1])

    @property
    def n_tracks(self) -> int:
        return int(self.tracks.shape[0])

    def frame(self, index: int, track: int = 0) -> ImageArray:
        """Decode timeline position ``index`` (wrapping) of ``track`` as a writable BGR image."""
        i = int(self.tracks[track % self.n_tracks, index % len(self)])
        start, end = int(self._index[i]), int(self._index[i + 1])
        import cv2  # lazy: the API reads status through this module without OpenCV

        buffer = np.frombuffer(self._data, dtype=np.uint8, count=end - start, offset=start)
        image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"frame {i} of the motion clip is corrupt")
        return np.asarray(image, dtype=np.uint8)
