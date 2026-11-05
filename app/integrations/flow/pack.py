"""Gói việc Flow — LiveFlow viết sẵn prompt, tên file đích và dự toán tín dụng.

Một "gói việc" (`FlowPack`) là một thư mục gồm:

    HUONG_DAN.txt         các bước làm trong Flow + bảng thiết lập cần chọn
    manifest.json         trạng thái từng việc (để LiveFlow nạp lại sau khi tắt app)
    prompts/01_....txt    prompt từng clip, mở ra là copy được
    anh_tham_chieu/       ảnh chân dung người mẫu / ảnh sản phẩm để kéo vào Flow

Người vận hành chỉ: dán prompt → bấm tạo → tải về. LiveFlow lo phần còn lại.
"""

from __future__ import annotations

import json
import re
import shutil
import time
import unicodedata
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from loguru import logger

from app.integrations.flow.constants import (
    APPEARANCE_FEMALE,
    APPEARANCE_MALE,
    AVATAR_CUES,
    BASE_LOOP_CUE,
    DEFAULT_FLOW_MODEL,
    FLOW_ASPECT_VERTICAL,
    FLOW_IMAGE_CREDITS,
    PRODUCT_PROMPT_SUFFIX,
    build_avatar_prompt,
    credits_for,
    flow_settings_hint,
    model_label,
)

MANIFEST_NAME = "manifest.json"

# Loại việc
KIND_PORTRAIT = "portrait"  # ảnh chân dung nhân vật (Ingredients) — tạo 1 lần
KIND_AVATAR_BASE = "avatar_base"  # clip loop nền của người mẫu
KIND_AVATAR_CUE = "avatar_cue"  # 1 trong 9 clip cử chỉ
KIND_PRODUCT_SCENE = "product_scene"  # 1 phân cảnh video bán hàng


def slugify(text: str, fallback: str = "nguoi_mau") -> str:
    """'Mai Phương' -> 'mai_phuong' (bỏ dấu, chỉ còn a-z0-9_)."""
    s = unicodedata.normalize("NFD", text or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.replace("đ", "d").replace("Đ", "D").lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or fallback


# ---------------------------------------------------------------------------
@dataclass
class FlowJob:
    """Một việc cần bấm trong Flow."""

    job_id: str
    order: int
    kind: str
    title: str  # tiêu đề tiếng Việt hiện trong giao diện
    prompt: str
    target_filename: str  # tên file sau khi LiveFlow chuẩn hoá
    credits: int = 0
    duration: int = 8
    cue: str = ""  # với clip người mẫu
    scene_index: int = 0  # với phân cảnh sản phẩm
    speech_text: str = ""  # thoại tiếng Việt (nếu để model tự nói)
    reference_image: str = ""  # ảnh kéo vào Ingredients / Frames
    status: str = "PENDING"  # PENDING | IMPORTED | SKIPPED
    imported_path: str = ""
    imported_at: float = 0.0
    note: str = ""

    @property
    def is_done(self) -> bool:
        return self.status == "IMPORTED"

    @property
    def prompt_filename(self) -> str:
        suffix = self.cue.lower() or (f"scene_{self.scene_index}" if self.scene_index else self.kind)
        return f"{self.order:02d}_{suffix}.txt"


@dataclass
class FlowPack:
    """Toàn bộ gói việc, lưu được ra đĩa và nạp lại."""

    pack_id: str
    kind: str  # "avatar" | "product"
    title: str
    directory: str
    model: str = DEFAULT_FLOW_MODEL
    resolution: str = "720p"
    aspect: str = FLOW_ASPECT_VERTICAL
    jobs: List[FlowJob] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    meta: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------- truy vấn
    @property
    def dir(self) -> Path:
        return Path(self.directory)

    @property
    def pending_jobs(self) -> List[FlowJob]:
        return [j for j in self.jobs if j.status == "PENDING"]

    @property
    def done_jobs(self) -> List[FlowJob]:
        return [j for j in self.jobs if j.status == "IMPORTED"]

    @property
    def credits_estimate(self) -> int:
        return sum(j.credits for j in self.jobs)

    @property
    def credits_remaining_estimate(self) -> int:
        return sum(j.credits for j in self.pending_jobs)

    def next_pending(self) -> Optional[FlowJob]:
        return self.pending_jobs[0] if self.pending_jobs else None

    def job(self, job_id: str) -> Optional[FlowJob]:
        return next((j for j in self.jobs if j.job_id == job_id), None)

    def is_complete(self) -> bool:
        return all(j.status in ("IMPORTED", "SKIPPED") for j in self.jobs)

    # --------------------------------------------------------------- lưu/nạp
    def save(self) -> Path:
        d = self.dir
        d.mkdir(parents=True, exist_ok=True)
        payload = asdict(self)
        path = d / MANIFEST_NAME
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def write_prompt_files(self) -> None:
        """Ghi từng prompt ra .txt — mở bằng Notepad là copy được, không cần app."""
        pdir = self.dir / "prompts"
        pdir.mkdir(parents=True, exist_ok=True)
        for job in self.jobs:
            if job.kind == KIND_PORTRAIT:
                header = "# ẢNH chân dung — tạo bằng chế độ tạo ẢNH trong Flow (không phải video)\n\n"
            else:
                header = f"# {job.title} — {job.duration}s — {job.credits} tín dụng\n\n"
            (pdir / job.prompt_filename).write_text(header + job.prompt + "\n", encoding="utf-8")

    def write_guide(self) -> Path:
        """Ghi HUONG_DAN.txt — đọc file này là làm được, không cần mở app."""
        dur = self.jobs[0].duration if self.jobs else 8
        lines: List[str] = [
            f"GÓI VIỆC FLOW: {self.title}",
            f"Mã gói: {self.pack_id}",
            "=" * 72,
            "",
            flow_settings_hint(self.model, self.resolution, dur),
            "",
            f"Dự toán: {len(self.jobs)} lần tạo × {model_label(self.model)} "
            f"= {self.credits_estimate} tín dụng.",
            "",
            "CÁC BƯỚC:",
            "  1. Mở https://flow.google.com/ , tạo một Project mới.",
            "  2. Đặt các thiết lập ở bảng trên (khung DỌC 9:16, outputs = 1).",
        ]
        portrait = next((j for j in self.jobs if j.kind == KIND_PORTRAIT), None)
        if portrait:
            lines += [
                f"  3. Làm việc số 01 trước ({portrait.title}) để có ảnh chân dung,",
                "     tải ảnh đó về rồi kéo vào ô 'Ingredients' — mọi clip sau dùng chung ảnh này",
                "     để MẶT NGƯỜI MẪU KHÔNG ĐỔI giữa các clip.",
            ]
        else:
            lines += [
                "  3. Kéo ảnh trong thư mục anh_tham_chieu/ vào ô 'Ingredients' (nếu có)",
                "     để giữ nguyên mặt người mẫu / đúng sản phẩm giữa các clip.",
            ]
        lines += [
            "  4. Lần lượt mở từng file trong prompts/ , copy toàn bộ, dán vào Flow, bấm tạo.",
            "  5. Clip xong thì bấm Download (bản gốc, KHÔNG cần upscale).",
            "  6. LiveFlow đang canh thư mục Downloads: file vừa tải sẽ tự được chuẩn hoá",
            "     về 1080x1920 và nạp vào đúng chỗ. Cứ tải tiếp clip sau.",
            "",
            "DANH SÁCH VIỆC:",
        ]
        for job in self.jobs:
            lines.append(
                f"  [{job.order:02d}] {job.title:<42} {job.credits:>4} tín dụng "
                f"-> {job.target_filename}"
            )
        lines += [
            "",
            "LƯU Ý TIẾT KIỆM TÍN DỤNG:",
            "  • 'Outputs per prompt' để 1. Để 2 là tốn gấp đôi cho cùng một prompt.",
            "  • Không bấm Upscale 4K nếu không thật cần — tốn thêm tín dụng mỗi video.",
            "  • Clip ra xấu thì sửa prompt rồi tạo lại; mỗi lần tạo lại vẫn trừ tín dụng.",
            "",
        ]
        path = self.dir / "HUONG_DAN.txt"
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def flush(self) -> None:
        """Ghi tất cả (manifest + prompt + hướng dẫn) ra đĩa."""
        self.save()
        self.write_prompt_files()
        self.write_guide()


def load_pack(path: Path | str) -> FlowPack:
    """Nạp gói việc từ thư mục hoặc từ đường dẫn manifest.json."""
    p = Path(path)
    if p.is_dir():
        p = p / MANIFEST_NAME
    raw = json.loads(p.read_text(encoding="utf-8"))
    jobs = [FlowJob(**j) for j in raw.pop("jobs", [])]
    pack = FlowPack(**raw)
    pack.jobs = jobs
    # Thư mục có thể đã bị di chuyển — lấy theo vị trí thật của manifest.
    pack.directory = str(p.parent)
    return pack


def list_packs(root: Path | str) -> List[FlowPack]:
    """Liệt kê mọi gói việc trong thư mục gốc, mới nhất trước."""
    packs: List[FlowPack] = []
    for manifest in Path(root).glob(f"*/{MANIFEST_NAME}"):
        try:
            packs.append(load_pack(manifest))
        except Exception as e:
            logger.warning("Bỏ qua gói việc lỗi {}: {}", manifest, e)
    return sorted(packs, key=lambda p: p.created_at, reverse=True)


# ---------------------------------------------------------------------------
# Trình dựng gói
# ---------------------------------------------------------------------------
def _copy_reference(pack_dir: Path, src: Optional[str | Path], name: str) -> str:
    if not src:
        return ""
    s = Path(src)
    if not s.exists():
        return ""
    dest_dir = pack_dir / "anh_tham_chieu"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{name}{s.suffix.lower() or '.jpg'}"
    try:
        shutil.copy2(s, dest)
        return str(dest)
    except Exception as e:
        logger.warning("Không copy được ảnh tham chiếu {}: {}", s, e)
        return ""


def build_avatar_pack(
    *,
    name: str = "Mai Phương",
    gender: str = "female",
    appearance: str = "",
    outfit_note: str = "",
    model: str = DEFAULT_FLOW_MODEL,
    resolution: str = "720p",
    duration: int = 8,
    green_screen: bool = True,
    include_cues: Iterable[str] | None = None,
    include_portrait: bool = True,
    portrait_image: Optional[str | Path] = None,
    credit_override: int = 0,
    image_credit_override: int = 0,
    root: Optional[Path | str] = None,
) -> FlowPack:
    """Gói việc tạo một người mẫu AI đầy đủ: 1 ảnh chân dung + 1 clip loop + 9 clip cử chỉ.

    Args:
        name: Tên người mẫu — quyết định tên file (``mai_phuong.mp4``).
        gender: ``female`` | ``male``.
        appearance: Mô tả ngoại hình tiếng Anh. Để trống thì dùng mẫu theo giới tính.
        outfit_note: Thêm vào mô tả (ví dụ ``wearing a red ao dai``).
        include_cues: Chỉ tạo các cue này. Mặc định: đủ 9 cue.
        include_portrait: Thêm việc tạo ảnh chân dung làm Ingredients.
        portrait_image: Đã có ảnh chân dung sẵn thì truyền vào, khỏi tạo mới.
        root: Thư mục chứa gói. Mặc định ``<data_dir>/flow_packs``.
    """
    slug = slugify(name)
    pack_id = f"flow_avatar_{slug}_{uuid.uuid4().hex[:6]}"
    pack_dir = Path(root or _default_root()) / pack_id
    pack_dir.mkdir(parents=True, exist_ok=True)

    look = appearance.strip() or (APPEARANCE_FEMALE if gender == "female" else APPEARANCE_MALE)
    ref = _copy_reference(pack_dir, portrait_image, f"{slug}_chan_dung")

    pack = FlowPack(
        pack_id=pack_id,
        kind="avatar",
        title=f"Người mẫu AI: {name}",
        directory=str(pack_dir),
        model=model,
        resolution=resolution,
        meta={
            "avatar_name": name,
            "avatar_slug": slug,
            "gender": gender,
            "appearance": look,
            "outfit_note": outfit_note,
            "green_screen": green_screen,
        },
    )

    order = 1
    per_clip = credits_for(model, credit_override)

    if include_portrait and not ref:
        pack.jobs.append(
            FlowJob(
                job_id=f"{pack_id}_portrait",
                order=order,
                kind=KIND_PORTRAIT,
                title=f"Ảnh chân dung {name} (Ingredients)",
                prompt=(
                    "Photorealistic vertical portrait photo, 9:16. "
                    f"{look}, standing facing the camera, framed from the knees up, "
                    "neutral friendly smile, even soft studio lighting, "
                    "flat uniform chroma key green screen background (pure #00FF00), no shadows on the background, "
                    "sharp focus, 4K. No text, no watermark, no other people."
                ),
                target_filename=f"{slug}.jpg",
                credits=image_credit_override or FLOW_IMAGE_CREDITS,
                duration=0,
                note="Tạo bằng chế độ tạo ẢNH của Flow, rồi kéo ảnh này vào Ingredients cho mọi clip sau.",
            )
        )
        order += 1

    # Clip loop nền: dùng cue IDLE_TALK, nhưng tên file không có hậu tố cue
    # để trùng quy ước storage/avatars/<ten>.mp4 mà app tự nhận.
    base_spec = next(c for c in AVATAR_CUES if c.cue == BASE_LOOP_CUE)
    pack.jobs.append(
        FlowJob(
            job_id=f"{pack_id}_base",
            order=order,
            kind=KIND_AVATAR_BASE,
            title=f"Clip nền (loop) — {base_spec.vi_label}",
            prompt=build_avatar_prompt(
                base_spec.action,
                appearance=look,
                duration=duration,
                green_screen=green_screen,
                outfit_note=outfit_note,
            ),
            target_filename=f"{slug}.mp4",
            credits=per_clip,
            duration=duration,
            cue=BASE_LOOP_CUE,
            reference_image=ref,
            note="Clip phát vòng lặp suốt phiên live khi không có cử chỉ đặc biệt.",
        )
    )
    order += 1

    wanted = set(include_cues) if include_cues is not None else {c.cue for c in AVATAR_CUES}
    for spec in AVATAR_CUES:
        # Clip nền ở trên đã là cue IDLE_TALK — không tạo lại (tiết kiệm 1 lần tín dụng).
        if spec.cue == BASE_LOOP_CUE or spec.cue not in wanted:
            continue
        pack.jobs.append(
            FlowJob(
                job_id=f"{pack_id}_{spec.cue.lower()}",
                order=order,
                kind=KIND_AVATAR_CUE,
                title=f"{spec.cue} — {spec.vi_label}",
                prompt=build_avatar_prompt(
                    spec.action,
                    appearance=look,
                    duration=duration,
                    green_screen=green_screen,
                    outfit_note=outfit_note,
                ),
                target_filename=f"{slug}_{spec.cue.lower()}.mp4",
                credits=per_clip,
                duration=duration,
                cue=spec.cue,
                reference_image=ref,
            )
        )
        order += 1

    pack.flush()
    logger.info(
        "Đã tạo gói việc Flow cho người mẫu '{}': {} việc, ước {} tín dụng -> {}",
        name, len(pack.jobs), pack.credits_estimate, pack_dir,
    )
    return pack


def build_product_pack(
    *,
    title: str,
    scenes: Iterable[Dict[str, Any]],
    project_id: str = "",
    product_id: int = 0,
    avatar_name: str = "",
    model: str = DEFAULT_FLOW_MODEL,
    resolution: str = "720p",
    native_speech: bool = True,
    credit_override: int = 0,
    product_image: Optional[str | Path] = None,
    root: Optional[Path | str] = None,
) -> FlowPack:
    """Gói việc render video bán hàng nhiều phân cảnh cho một sản phẩm.

    Args:
        scenes: Mỗi phần tử gồm ``scene_index``, ``shot_type``, ``veo_prompt``,
            ``speech_text``, ``duration_seconds`` và tuỳ chọn ``reference_frame``.
        native_speech: True = ghép câu thoại tiếng Việt vào prompt để model tự nói
            (khớp khẩu hình). False = clip câm, LiveFlow ghép Edge-TTS vào sau.
    """
    pack_id = project_id or f"flow_product_{uuid.uuid4().hex[:8]}"
    pack_dir = Path(root or _default_root()) / pack_id
    pack_dir.mkdir(parents=True, exist_ok=True)

    pack = FlowPack(
        pack_id=pack_id,
        kind="product",
        title=title,
        directory=str(pack_dir),
        model=model,
        resolution=resolution,
        meta={
            "product_id": product_id,
            "avatar_name": avatar_name,
            "native_speech": native_speech,
            "project_id": project_id,
        },
    )

    prod_ref = _copy_reference(pack_dir, product_image, "san_pham")
    per_clip = credits_for(model, credit_override)

    for order, scene in enumerate(scenes, start=1):
        idx = int(scene.get("scene_index", order))
        shot = str(scene.get("shot_type", "DEMO"))
        speech = str(scene.get("speech_text", "")).strip()
        duration = int(scene.get("duration_seconds", 8) or 8)
        base_prompt = str(scene.get("veo_prompt", "")).strip()

        parts = [base_prompt]
        if native_speech and speech:
            parts.append(
                "DIALOGUE: the presenter looks at the camera and says this line in Vietnamese "
                "with natural energetic delivery and matching lip movement: "
                f'"{speech}"'
            )
            parts.append("AUDIO: her voice only, no background music.")
        else:
            parts.append("AUDIO: ambient only, no speech, lips closed.")
        parts.append(PRODUCT_PROMPT_SUFFIX)

        ref = _copy_reference(pack_dir, scene.get("reference_frame"), f"canh_{idx}_khung_dau") or prod_ref

        pack.jobs.append(
            FlowJob(
                job_id=f"{pack_id}_scene_{idx}",
                order=order,
                kind=KIND_PRODUCT_SCENE,
                title=f"Cảnh {idx} — {shot}",
                prompt="\n".join(p for p in parts if p),
                target_filename=f"{pack_id}_scene_{idx}_raw.mp4",
                credits=per_clip,
                duration=duration,
                scene_index=idx,
                speech_text=speech,
                reference_image=ref,
                note="Kéo ảnh khung đầu vào 'Frames to Video' để đúng người mẫu + đúng sản phẩm.",
            )
        )

    pack.flush()
    logger.info(
        "Đã tạo gói việc Flow cho sản phẩm '{}': {} cảnh, ước {} tín dụng -> {}",
        title, len(pack.jobs), pack.credits_estimate, pack_dir,
    )
    return pack


def _default_root() -> Path:
    try:
        from app.config.settings import get_settings

        return get_settings().effective_flow_dir
    except Exception:  # pragma: no cover
        d = Path.cwd() / "storage" / "flow_packs"
        d.mkdir(parents=True, exist_ok=True)
        return d
