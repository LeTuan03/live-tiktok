"""LivePortrait portrait animation on ONNX Runtime + OpenVINO (offline only).

Implements the published LivePortrait inference recipe without PyTorch:

1. crop the face (scale 2.3 around the landmarks, as in training);
2. encode the crop once into a 3D appearance volume and canonical keypoints;
3. per frame, pose the keypoints with the requested rotation, translation and
   expression, run the stitching network so the crop border stays glued to
   the untouched body, and decode a 512x512 image with the warping network;
4. paste the crop back into the full frame through a feathered mask.

Only the crop region is ever regenerated. Everything outside it (torso,
hands, background) is the original photo.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import numpy.typing as npt
import onnxruntime as ort  # type: ignore[import-untyped]

from soshop.agent.lipsync.facedet import FaceAnalyzer
from soshop.agent.lipsync.models import liveportrait_dir, warp_stage_paths
from soshop.agent.lipsync.motion import MotionFrame

logger = logging.getLogger("soshop.agent.lipsync")

FloatArray = npt.NDArray[np.float32]
ImageArray = npt.NDArray[np.uint8]

_CROP_SIZE = 512
_MODEL_SIZE = 256
_CROP_SCALE = 2.3
_CROP_VY_RATIO = -0.125
# 106-landmark indices LivePortrait uses to find the eye and lip centers.
_LEFT_EYE = (33, 35, 40, 39)
_RIGHT_EYE = (87, 89, 94, 93)
_LIP = (52, 61)
# Midpoints of the inner upper and lower lip. Their gap over the mouth width
# is LivePortrait's "lip close ratio".
_INNER_LIP = (62, 60)
# Above this ratio the portrait's mouth counts as open and is closed before
# animating (LivePortrait's own `lip_normalize_threshold`).
_LIP_OPEN_THRESHOLD = 0.03


@dataclass(frozen=True)
class Source:
    """Everything derived once from the portrait."""

    frame: ImageArray
    crop_to_frame: FloatArray
    mask: FloatArray
    """(H, W, 1) paste weight of the regenerated crop in the full frame."""
    feature: FloatArray
    kp_canonical: FloatArray
    kp_source: FloatArray
    exp: FloatArray
    scale: float
    translation: FloatArray
    pitch: float
    yaw: float
    roll: float
    lip_ratio: float = 0.0
    """Lip opening of the portrait itself (inner lip gap over mouth width)."""


def _rotation(pitch: float, yaw: float, roll: float) -> FloatArray:
    x, y, z = (math.radians(v) for v in (pitch, yaw, roll))
    rot_x = np.array([[1, 0, 0], [0, math.cos(x), -math.sin(x)], [0, math.sin(x), math.cos(x)]])
    rot_y = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    rot_z = np.array([[math.cos(z), -math.sin(z), 0], [math.sin(z), math.cos(z), 0], [0, 0, 1]])
    return (rot_z @ rot_y @ rot_x).T.astype(np.float32)


def _degrees(logits: FloatArray) -> float:
    """Decode a 66-bin head pose classification into degrees."""
    shifted = logits.reshape(-1) - logits.max()
    prob = np.exp(shifted) / np.exp(shifted).sum()
    return float((prob * np.arange(66)).sum() * 3 - 97.5)


def _crop_transform(landmarks: FloatArray) -> FloatArray:
    """Frame-to-crop affine (2x3), following LivePortrait's crop recipe."""
    eye = (landmarks[list(_LEFT_EYE)].mean(axis=0) + landmarks[list(_RIGHT_EYE)].mean(axis=0)) / 2
    lip = landmarks[list(_LIP)].mean(axis=0)
    uy = lip - eye
    norm = float(np.linalg.norm(uy))
    uy = np.array([0.0, 1.0], dtype=np.float32) if norm <= 1e-3 else uy / norm
    ux = np.array([uy[1], -uy[0]], dtype=np.float32)
    center0 = landmarks.mean(axis=0)
    rotated = (landmarks - center0) @ np.stack([ux, uy]).T
    low, high = rotated.min(axis=0), rotated.max(axis=0)
    center1 = (low + high) / 2
    size = float((high - low).max()) * _CROP_SCALE
    center = center0 + ux * center1[0] + uy * center1[1] + uy * (_CROP_VY_RATIO * size)

    angle = math.acos(float(np.clip(ux[0], -1.0, 1.0)))
    if ux[1] < 0:
        angle = -angle
    scale = _CROP_SIZE / size
    cos, sin = math.cos(angle) * scale, math.sin(angle) * scale
    half = _CROP_SIZE / 2
    return np.array(
        [
            [cos, sin, half - (cos * center[0] + sin * center[1])],
            [-sin, cos, half - (-sin * center[0] + cos * center[1])],
        ],
        dtype=np.float32,
    )


def _paste_mask() -> FloatArray:
    """Soft rounded mask in crop space: 1 inside, fading to 0 at the border."""
    mask = np.zeros((_CROP_SIZE, _CROP_SIZE), dtype=np.float32)
    inset = int(_CROP_SIZE * 0.11)
    radius = int(_CROP_SIZE * 0.16)
    cv2.rectangle(
        mask, (inset + radius, inset), (_CROP_SIZE - inset - radius, _CROP_SIZE - inset), 1.0, -1
    )
    cv2.rectangle(
        mask, (inset, inset + radius), (_CROP_SIZE - inset, _CROP_SIZE - inset - radius), 1.0, -1
    )
    for cx in (inset + radius, _CROP_SIZE - inset - radius):
        for cy in (inset + radius, _CROP_SIZE - inset - radius):
            cv2.circle(mask, (cx, cy), radius, 1.0, -1)
    return np.asarray(cv2.GaussianBlur(mask, (0, 0), _CROP_SIZE * 0.035), dtype=np.float32)


class _WarpingNetwork:
    """Runs the five warping stages in order over a shared tensor environment."""

    def __init__(self, stage_paths: list[Path]) -> None:
        import openvino as ov  # type: ignore[import-untyped]

        core = ov.Core()
        gpu = _intel_gpu(core)
        options = ort.SessionOptions()
        options.log_severity_level = 3
        self._stages: list[Any] = []
        for index, path in enumerate(stage_paths):
            if index % 2 == 1:
                self._stages.append(
                    ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
                )
                continue
            self._stages.append(_compile(core, path, gpu))

    def __call__(self, inputs: dict[str, FloatArray]) -> FloatArray:
        env = dict(inputs)
        for stage in self._stages:
            if isinstance(stage, ort.InferenceSession):
                names = [o.name for o in stage.get_outputs()]
                values = stage.run(None, {i.name: env[i.name] for i in stage.get_inputs()})
                env.update(zip(names, values, strict=True))
            else:
                result = stage({i.get_any_name(): env[i.get_any_name()] for i in stage.inputs})
                for output in stage.outputs:
                    env[output.get_any_name()] = result[output]
        return env["out"]


def _intel_gpu(core: Any) -> str | None:
    for device in core.available_devices:
        if device.startswith("GPU") and "Intel" in core.get_property(device, "FULL_DEVICE_NAME"):
            return str(device)
    return None


def _compile(core: Any, path: Path, gpu: str | None) -> Any:
    """Compile on the Intel GPU when it accepts the graph, else on the CPU."""
    if gpu is not None:
        try:
            # The GPU default precision (f16) differs from f32 by at most
            # 5/255 per pixel here and is nearly twice as fast.
            return core.compile_model(str(path), gpu)
        except Exception:
            logger.info("%s does not compile on %s, using CPU", path.name, gpu)
    return core.compile_model(str(path), "CPU")


class LivePortrait:
    def __init__(self, models_dir: Path) -> None:
        base = liveportrait_dir(models_dir)
        options = ort.SessionOptions()
        options.log_severity_level = 3
        providers = ["CPUExecutionProvider"]
        self.faces = FaceAnalyzer(base)
        self._appearance = ort.InferenceSession(
            str(base / "appearance_feature_extractor.onnx"), options, providers=providers
        )
        self._motion = ort.InferenceSession(
            str(base / "motion_extractor.onnx"), options, providers=providers
        )
        self._stitch = ort.InferenceSession(
            str(base / "stitching.onnx"), options, providers=providers
        )
        self._lip = ort.InferenceSession(
            str(base / "stitching_lip.onnx"), options, providers=providers
        )
        self._warp = _WarpingNetwork(warp_stage_paths(models_dir))

    @staticmethod
    def _lip_ratio(landmarks: FloatArray) -> float:
        mouth_width = float(np.linalg.norm(landmarks[_LIP[0]] - landmarks[_LIP[1]]))
        gap = float(np.linalg.norm(landmarks[_INNER_LIP[0]] - landmarks[_INNER_LIP[1]]))
        return gap / mouth_width if mouth_width > 0 else 0.0

    def _retarget_lip(self, kp_source: FloatArray, current: float, wanted: float) -> FloatArray:
        """Posed-keypoint offsets that take the lips from ``current`` to ``wanted`` openness."""
        feed = np.concatenate([kp_source.reshape(-1), [current, wanted]])[None].astype(np.float32)
        delta: FloatArray = self._lip.run(None, {"input": feed})[0].reshape(21, 3)
        return delta

    def _closed_mouth_delta(self, landmarks: FloatArray, kp_source: FloatArray) -> FloatArray:
        """Keypoint offsets that close the portrait's mouth, zeros if already closed.

        A portrait caught mid-laugh would otherwise idle with its mouth open
        for the whole stream, and the realtime lip sync would snap between
        that laugh and speech. LivePortrait's lip retargeting network maps
        (source keypoints, current openness, wanted openness) to the offsets
        that reach it; openness 0 gives a relaxed closed-lip expression.
        """
        ratio = self._lip_ratio(landmarks)
        if ratio < _LIP_OPEN_THRESHOLD:
            return np.zeros((21, 3), dtype=np.float32)
        logger.info("portrait mouth is open (ratio %.2f): closing it for the idle clip", ratio)
        return self._retarget_lip(kp_source, ratio, 0.0)

    def lip_offset(self, source: Source, openness: float) -> FloatArray:
        """Expression offset that opens the (closed) mouth to ``openness``.

        ``openness`` is LivePortrait's lip ratio: 0 closed, about 0.3 a wide
        open vowel. The result adds to ``MotionFrame.exp``. Driving speech
        this way moves the jaw and cheeks and keeps the portrait's own teeth
        at full resolution, unlike a 96 px lip-sync model.
        """
        if openness <= 0.0:
            return np.zeros((21, 3), dtype=np.float32)
        opened = self._retarget_lip(source.kp_source, source.lip_ratio, openness)
        closed = self._retarget_lip(source.kp_source, source.lip_ratio, 0.0)
        return ((opened - closed) / source.scale).astype(np.float32)

    def prepare(self, frame: ImageArray) -> Source:
        """Analyze the portrait (BGR, already at output size)."""
        face = self.faces.detect(frame)
        if face is None:
            raise ValueError("no face found in the portrait")
        landmarks = self.faces.landmarks(frame, face)
        frame_to_crop = _crop_transform(landmarks)
        crop = cv2.warpAffine(
            frame, frame_to_crop, (_CROP_SIZE, _CROP_SIZE), flags=cv2.INTER_LINEAR
        )
        small = cv2.resize(crop, (_MODEL_SIZE, _MODEL_SIZE), interpolation=cv2.INTER_AREA)
        blob = (small[:, :, ::-1].astype(np.float32) / 255.0).transpose(2, 0, 1)[None]
        blob = np.ascontiguousarray(blob)

        feature = self._appearance.run(None, {"img": blob})[0]
        pitch, yaw, roll, t, exp, scale, kp = self._motion.run(None, {"img": blob})
        pitch_deg, yaw_deg, roll_deg = _degrees(pitch), _degrees(yaw), _degrees(roll)
        kp_canonical = kp.reshape(21, 3)
        exp = exp.reshape(21, 3)
        translation = t.reshape(3).copy()
        translation[2] = 0.0
        scale_value = float(scale.reshape(-1)[0])
        kp_source = (
            scale_value * (kp_canonical @ _rotation(pitch_deg, yaw_deg, roll_deg) + exp)
            + translation
        )

        # The offsets live in posed keypoint space; `render` poses keypoints as
        # scale * (canonical @ R + exp) + t, so they fold into the expression
        # term divided by the scale. `kp_source` stays the true source pose:
        # the warp needs to know where the pixels actually are.
        lip_delta = self._closed_mouth_delta(landmarks, kp_source.astype(np.float32))
        exp = exp + lip_delta / scale_value

        crop_to_frame = cv2.invertAffineTransform(frame_to_crop).astype(np.float32)
        height, width = frame.shape[:2]
        mask = cv2.warpAffine(_paste_mask(), crop_to_frame, (width, height))
        return Source(
            frame=frame,
            crop_to_frame=crop_to_frame,
            mask=mask[:, :, None].astype(np.float32),
            feature=feature,
            kp_canonical=kp_canonical,
            kp_source=kp_source.astype(np.float32),
            exp=exp,
            scale=scale_value,
            translation=translation,
            pitch=pitch_deg,
            yaw=yaw_deg,
            roll=roll_deg,
            lip_ratio=self._lip_ratio(landmarks),
        )

    def render(self, source: Source, motion: MotionFrame) -> ImageArray:
        """Render one full frame (BGR) with ``motion`` applied to the head."""
        rotation = _rotation(
            source.pitch + motion.pitch, source.yaw + motion.yaw, source.roll + motion.roll
        )
        translation = source.translation + np.array([motion.tx, motion.ty, 0.0], dtype=np.float32)
        kp_driving = (
            source.scale * (source.kp_canonical @ rotation + source.exp + motion.exp) + translation
        ).astype(np.float32)

        stitch_in = np.concatenate([source.kp_source.reshape(-1), kp_driving.reshape(-1)])[None]
        delta = self._stitch.run(None, {"input": stitch_in.astype(np.float32)})[0].reshape(-1)
        kp_driving = kp_driving + delta[:63].reshape(21, 3)
        kp_driving[:, :2] += delta[63:65]

        out = self._warp(
            {
                "feature_3d": source.feature,
                "kp_driving": kp_driving[None].astype(np.float32),
                "kp_source": source.kp_source[None],
            }
        )
        crop = np.clip(out[0].transpose(1, 2, 0)[:, :, ::-1] * 255.0, 0, 255).astype(np.float32)
        height, width = source.frame.shape[:2]
        warped = cv2.warpAffine(crop, source.crop_to_frame, (width, height), flags=cv2.INTER_LINEAR)
        blended = source.mask * warped + (1.0 - source.mask) * source.frame.astype(np.float32)
        return np.clip(blended, 0, 255).astype(np.uint8)
