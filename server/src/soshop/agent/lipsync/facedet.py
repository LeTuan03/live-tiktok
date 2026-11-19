"""Face detection and 106-point landmarks (InsightFace SCRFD + 2d106det).

Used offline only: once on the portrait to place the LivePortrait crop, and
once per rendered frame to locate the face box the realtime lip sync repaints.
Both models run on ONNX Runtime's CPU provider; they are small.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt
import onnxruntime as ort  # type: ignore[import-untyped]

FloatArray = npt.NDArray[np.float32]
ImageArray = npt.NDArray[np.uint8]

_DET_SIZE = 512
_DET_STRIDES = (8, 16, 32)
_DET_ANCHORS = 2
_LMK_SIZE = 192


@dataclass(frozen=True)
class Face:
    box: FloatArray
    """(x0, y0, x1, y1) in image pixels."""
    score: float


class FaceAnalyzer:
    def __init__(self, liveportrait_dir: Path) -> None:
        options = ort.SessionOptions()
        options.log_severity_level = 3
        providers = ["CPUExecutionProvider"]
        self._det = ort.InferenceSession(
            str(liveportrait_dir / "retinaface_det_static.onnx"), options, providers=providers
        )
        self._lmk = ort.InferenceSession(
            str(liveportrait_dir / "face_2dpose_106_static.onnx"), options, providers=providers
        )
        self._det_input = self._det.get_inputs()[0].name
        self._lmk_input = self._lmk.get_inputs()[0].name

    def detect(self, image: ImageArray, *, threshold: float = 0.5) -> Face | None:
        """Return the most confident face in a BGR image, or None."""
        height, width = image.shape[:2]
        scale = _DET_SIZE / max(height, width)
        resized = cv2.resize(image, (round(width * scale), round(height * scale)))
        canvas = np.zeros((_DET_SIZE, _DET_SIZE, 3), dtype=np.uint8)
        canvas[: resized.shape[0], : resized.shape[1]] = resized
        blob = (canvas[:, :, ::-1].astype(np.float32) - 127.5) / 128.0
        outputs = self._det.run(None, {self._det_input: blob.transpose(2, 0, 1)[None]})

        best: Face | None = None
        for level, stride in enumerate(_DET_STRIDES):
            scores = outputs[level].reshape(-1)
            index = int(scores.argmax())
            score = float(scores[index])
            if score < threshold or (best is not None and score <= best.score):
                continue
            side = _DET_SIZE // stride
            cell = index // _DET_ANCHORS
            center = np.array([cell % side, cell // side], dtype=np.float32) * stride
            dist = outputs[level + len(_DET_STRIDES)][index] * stride
            box = np.array(
                [
                    center[0] - dist[0],
                    center[1] - dist[1],
                    center[0] + dist[2],
                    center[1] + dist[3],
                ],
                dtype=np.float32,
            )
            best = Face(box=box / scale, score=score)
        return best

    def landmarks(self, image: ImageArray, face: Face) -> FloatArray:
        """Return the 106 landmarks of ``face`` as (106, 2) image pixels."""
        x0, y0, x1, y1 = face.box
        center = np.array([(x0 + x1) / 2, (y0 + y1) / 2], dtype=np.float32)
        scale = _LMK_SIZE / (max(x1 - x0, y1 - y0) * 1.5)
        matrix = np.array(
            [
                [scale, 0.0, _LMK_SIZE / 2 - center[0] * scale],
                [0.0, scale, _LMK_SIZE / 2 - center[1] * scale],
            ],
            dtype=np.float32,
        )
        aligned = cv2.warpAffine(image, matrix, (_LMK_SIZE, _LMK_SIZE), borderValue=0.0)
        blob = aligned[:, :, ::-1].astype(np.float32).transpose(2, 0, 1)[None]
        pred = self._lmk.run(None, {self._lmk_input: blob})[0].reshape(-1, 2)
        points = (pred + 1.0) * (_LMK_SIZE / 2)
        inverse = cv2.invertAffineTransform(matrix)
        return (points @ inverse[:, :2].T + inverse[:, 2]).astype(np.float32)
