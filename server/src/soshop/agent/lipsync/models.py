"""Model weights for the lip-sync avatar: locations, download, preparation.

Weights are fetched on first use into ``models_dir`` and never committed.
See the package ``README.md`` for where each file comes from and its license.

The LivePortrait warping network cannot be loaded by OpenVINO as shipped: it
contains two 5D ``GridSample`` nodes. Run whole on ONNX Runtime's CPU provider
it takes 6 to 8 s per frame on a laptop CPU because of its 3D convolutions.
:func:`ensure_warp_stages` cuts the graph around those two nodes, so the
convolution stages can run on an Intel GPU through OpenVINO and only the two
tiny ``GridSample`` stages stay on ONNX Runtime (about 1 s per frame total).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import httpx

logger = logging.getLogger("soshop.agent.lipsync")

_HF = "https://huggingface.co/{repo}/resolve/main/{path}"

_LIVEPORTRAIT_REPO = "warmshao/FasterLivePortrait"
_WAV2LIP_REPO = "bluefoxcreation/Wav2lip-Onnx"
_MODNET_REPO = "Xenova/modnet"
_MODNET_FILE = "modnet.onnx"

LIVEPORTRAIT_FILES = (
    "appearance_feature_extractor.onnx",
    "motion_extractor.onnx",
    "stitching.onnx",
    "stitching_lip.onnx",
    "retinaface_det_static.onnx",
    "face_2dpose_106_static.onnx",
)
_WARP_FILE = "warping_spade.onnx"
WAV2LIP_FILE = "wav2lip_gan.onnx"
WARP_STAGE_COUNT = 5

ProgressFn = Callable[[float], None]


def liveportrait_dir(models_dir: Path) -> Path:
    return models_dir / "liveportrait"


def wav2lip_path(models_dir: Path) -> Path:
    return models_dir / "wav2lip" / WAV2LIP_FILE


def warp_stage_paths(models_dir: Path) -> list[Path]:
    split = liveportrait_dir(models_dir) / "split"
    return [split / f"warp_stage{i}.onnx" for i in range(WARP_STAGE_COUNT)]


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(dest.suffix + ".part")
    logger.info("downloading %s", url)
    with (
        httpx.stream("GET", url, follow_redirects=True, timeout=60.0) as resp,
        partial.open("wb") as out,
    ):
        resp.raise_for_status()
        for chunk in resp.iter_bytes(1 << 20):
            out.write(chunk)
    partial.replace(dest)


def ensure_wav2lip(models_dir: Path) -> Path:
    """Return the Wav2Lip model path, downloading it if missing."""
    path = wav2lip_path(models_dir)
    if not path.exists():
        _download(_HF.format(repo=_WAV2LIP_REPO, path=WAV2LIP_FILE), path)
    return path


def modnet_path(models_dir: Path) -> Path:
    return models_dir / "modnet" / _MODNET_FILE


def ensure_modnet(models_dir: Path) -> Path:
    """Return the MODNet portrait matting model path, downloading it if missing."""
    path = modnet_path(models_dir)
    if not path.exists():
        _download(_HF.format(repo=_MODNET_REPO, path="onnx/model.onnx"), path)
    return path


def ensure_liveportrait(models_dir: Path, progress: ProgressFn | None = None) -> Path:
    """Download the LivePortrait models and prepare the split warping stages."""
    base = liveportrait_dir(models_dir)
    stages = warp_stage_paths(models_dir)
    need_warp = not all(p.exists() for p in stages)
    steps = len(LIVEPORTRAIT_FILES) + (2 if need_warp else 0)
    done = 0

    def _tick() -> None:
        nonlocal done
        done += 1
        if progress is not None:
            progress(done / steps)

    for name in LIVEPORTRAIT_FILES:
        if not (base / name).exists():
            _download(
                _HF.format(repo=_LIVEPORTRAIT_REPO, path=f"liveportrait_onnx/{name}"),
                base / name,
            )
        _tick()
    if need_warp:
        warp = base / _WARP_FILE
        if not warp.exists():
            _download(
                _HF.format(repo=_LIVEPORTRAIT_REPO, path=f"liveportrait_onnx/{_WARP_FILE}"),
                warp,
            )
        _tick()
        split_warping_model(warp, stages)
        warp.unlink()  # the stages replace it; saves 420 MB
        _tick()
    return base


def split_warping_model(source: Path, stages: list[Path]) -> None:
    """Cut the warping graph into 5 stages around its two ``GridSample`` nodes.

    Even stages hold ordinary operators, odd stages hold one ``GridSample``
    each. Every tensor that crosses a cut becomes a stage output and an input
    of the later stage that reads it, so the stages compose back into the
    original graph when run in order with a shared tensor environment.
    """
    import onnx
    from onnx import helper

    model = onnx.load(str(source))
    graph = model.graph
    initializers = {i.name: i for i in graph.initializer}
    grid_nodes = [n.name for n in graph.node if n.op_type == "GridSample"]
    if len(grid_nodes) * 2 + 1 != len(stages):
        raise RuntimeError(f"unexpected GridSample count {len(grid_nodes)} in {source.name}")

    tensor_stage: dict[str, int] = {}
    node_stage: dict[str, int] = {}
    for node in graph.node:  # ONNX graphs are topologically sorted
        if node.name in grid_nodes:
            stage = 1 + 2 * grid_nodes.index(node.name)
        else:
            stage = max(
                (tensor_stage.get(i, 0) for i in node.input if i and i not in initializers),
                default=0,
            )
            stage += stage % 2  # a GridSample's consumers start the next stage
        node_stage[node.name] = stage
        for output in node.output:
            tensor_stage[output] = stage

    inferred = onnx.shape_inference.infer_shapes(model).graph
    infos = {v.name: v for v in [*inferred.value_info, *graph.input, *graph.output]}
    final_outputs = {o.name for o in graph.output}

    stages[0].parent.mkdir(parents=True, exist_ok=True)
    for index, path in enumerate(stages):
        nodes = [n for n in graph.node if node_stage[n.name] == index]
        produced = {o for n in nodes for o in n.output}
        used_later = {
            i for n in graph.node if node_stage[n.name] > index for i in n.input
        } | final_outputs
        inputs = dict.fromkeys(
            i for n in nodes for i in n.input if i and i not in initializers and i not in produced
        )
        outputs = [o for n in nodes for o in n.output if o in used_later]
        weights = {i: initializers[i] for n in nodes for i in n.input if i in initializers}
        sub = helper.make_graph(
            nodes,
            f"warp_stage{index}",
            [infos[i] for i in inputs],
            [infos[o] for o in outputs],
            list(weights.values()),
        )
        onnx.save(
            helper.make_model(sub, opset_imports=model.opset_import, ir_version=model.ir_version),
            str(path),
        )
