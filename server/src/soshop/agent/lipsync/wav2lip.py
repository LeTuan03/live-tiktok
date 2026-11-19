"""Wav2Lip inference engine for the realtime lip sync.

The model takes 200 ms of mel spectrogram plus a 96x96 face crop whose lower
half is blanked (with the unblanked crop as reference) and returns the crop
with a mouth that matches the audio.

It runs through OpenVINO. On a laptop with an Intel iGPU that is about 15 ms
per frame on the GPU against 30 to 50 ms on the CPU, and it leaves the CPU
free for the rest of the agent. Every candidate device is compiled and timed
at startup and the fastest one wins, so machines without an Intel GPU fall
back to the CPU on their own.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from soshop.agent.lipsync.mel import MEL_STEP, N_MELS

logger = logging.getLogger("soshop.agent.lipsync")

FACE_SIZE = 96

FloatArray = npt.NDArray[np.float32]
ImageArray = npt.NDArray[np.uint8]


class Wav2LipEngine:
    def __init__(self, model_path: Path) -> None:
        import openvino as ov  # type: ignore[import-untyped]

        core = ov.Core()
        model = core.read_model(str(model_path))
        model.reshape({"mel": [1, 1, N_MELS, MEL_STEP], "vid": [1, 6, FACE_SIZE, FACE_SIZE]})
        cache_dir = model_path.parent / "ov_cache"

        candidates = [
            d
            for d in core.available_devices
            if d.startswith("GPU") and "Intel" in core.get_property(d, "FULL_DEVICE_NAME")
        ]
        candidates.append("CPU")
        best: tuple[float, str, Any] | None = None
        for device in candidates:
            try:
                compiled = core.compile_model(
                    model, device, {"PERFORMANCE_HINT": "LATENCY", "CACHE_DIR": str(cache_dir)}
                )
                elapsed = self._time(compiled)
            except Exception:
                logger.info("wav2lip: device %s unavailable", device, exc_info=True)
                continue
            logger.info("wav2lip: %s takes %.1f ms per frame", device, elapsed * 1000)
            if best is None or elapsed < best[0]:
                best = (elapsed, device, compiled)
        if best is None:
            raise RuntimeError("no OpenVINO device could load the lip-sync model")
        self.frame_seconds, self.device, self._compiled = best
        self._output = self._compiled.output(0)

    @staticmethod
    def _time(compiled: Any) -> float:
        feed = {
            "mel": np.zeros((1, 1, N_MELS, MEL_STEP), dtype=np.float32),
            "vid": np.zeros((1, 6, FACE_SIZE, FACE_SIZE), dtype=np.float32),
        }
        compiled(feed)  # first call includes lazy initialization
        runs = 4
        start = time.perf_counter()
        for _ in range(runs):
            compiled(feed)
        return (time.perf_counter() - start) / runs

    def infer(self, mel: FloatArray, face: ImageArray) -> ImageArray:
        """Lip-sync one face.

        ``mel`` is ``(N_MELS, MEL_STEP)``, ``face`` a ``96x96`` BGR crop.
        Returns the repainted ``96x96`` BGR crop.
        """
        # The exported network works in RGB. Fed BGR it still copies skin
        # correctly from the reference, but everything it has to invent (lips,
        # tongue) comes out with red and blue swapped: purple lips.
        reference = face[:, :, ::-1].astype(np.float32) / 255.0
        masked = reference.copy()
        masked[FACE_SIZE // 2 :] = 0.0
        video = np.concatenate([masked, reference], axis=2).transpose(2, 0, 1)[None]
        feed = {"mel": mel[None, None].astype(np.float32), "vid": np.ascontiguousarray(video)}
        out = self._compiled(feed)[self._output]
        rgb = np.clip(out[0].transpose(1, 2, 0) * 255.0, 0, 255).astype(np.uint8)
        image: ImageArray = np.ascontiguousarray(rgb[:, :, ::-1])
        return image
