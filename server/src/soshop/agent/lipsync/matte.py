"""Person matte for the realtime body motion.

``performance.Performer`` moves the body (breathing, sway, lean) and should
leave the background where it is. That needs to know which pixels are the
person. MODNet, a small portrait matting network (Apache-2.0, about 26 MB),
answers that once per clip from its first frame in a fraction of a second on
the CPU. The matte is blurred heavily before use, so its edge quality does not
matter much, only that the body and hair are covered.
"""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt

from soshop.agent.lipsync.models import modnet_path

logger = logging.getLogger("soshop.agent.lipsync")

FloatArray = npt.NDArray[np.float32]
ImageArray = npt.NDArray[np.uint8]

_SHORT_SIDE = 512
_MULTIPLE = 32


def _model_size(width: int, height: int) -> tuple[int, int]:
    """Input size MODNet expects: short side 512, both sides multiples of 32."""
    scale = _SHORT_SIDE / min(width, height)

    def fit(value: int) -> int:
        return max(_MULTIPLE, int(round(value * scale / _MULTIPLE)) * _MULTIPLE)

    return fit(width), fit(height)


def person_matte(models_dir: Path, frame: ImageArray) -> FloatArray:
    """Alpha matte of the person in a BGR frame, ``(H, W)`` float in ``[0, 1]``.

    Raises ``FileNotFoundError`` when the model has not been downloaded.
    """
    import onnxruntime as ort  # type: ignore[import-untyped]

    path = modnet_path(models_dir)
    if not path.is_file():
        raise FileNotFoundError(path)
    options = ort.SessionOptions()
    options.log_severity_level = 3
    session = ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])

    height, width = frame.shape[:2]
    size = _model_size(width, height)
    rgb = cv2.cvtColor(cv2.resize(frame, size, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
    blob = ((rgb.astype(np.float32) / 255.0 - 0.5) / 0.5).transpose(2, 0, 1)[None]
    alpha = session.run(None, {"input": np.ascontiguousarray(blob)})[0][0, 0]
    full = cv2.resize(alpha.astype(np.float32), (width, height), interpolation=cv2.INTER_LINEAR)
    return np.clip(np.asarray(full, dtype=np.float32), 0.0, 1.0)


def save_matte(path: Path, matte: FloatArray) -> None:
    cv2.imwrite(str(path), np.round(matte * 255).astype(np.uint8))


def load_matte(path: Path) -> FloatArray | None:
    if not path.is_file():
        return None
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return None
    return (image.astype(np.float32) / 255.0).astype(np.float32)
