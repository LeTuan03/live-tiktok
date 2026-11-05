"""Hằng số, bảng tín dụng và khuôn prompt cho Google Flow.

Số tín dụng dưới đây là **mức Google công bố cho người dùng gói AI Pro** (không phải Ultra).
Google có thể đổi giá, nên mọi con số đều ghi đè được bằng `.env`
(`LIVEFLOW_FLOW_CREDIT_COST`, `LIVEFLOW_FLOW_IMAGE_CREDIT_COST`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

FLOW_URL = "https://flow.google.com/"
FLOW_HELP_URL = "https://support.google.com/flow"

# Flow xuất video 24fps, 720p hoặc 1080p, khung 16:9 hoặc 9:16 (dọc).
FLOW_FPS = 24
FLOW_RESOLUTIONS: Tuple[str, ...] = ("720p", "1080p")
FLOW_ASPECT_VERTICAL = "9:16"

# Flow chỉ cho chọn độ dài theo bước 4/6/8 giây (tuỳ model). 8s là mức cho nhiều cử chỉ nhất.
FLOW_DURATIONS: Tuple[int, ...] = (4, 6, 8)


@dataclass(frozen=True)
class FlowModel:
    """Một lựa chọn model trong Flow kèm giá tín dụng mỗi lần tạo."""

    key: str
    label: str  # tên đúng như hiện trong menu của Flow
    credits: int  # tín dụng cho MỖI video sinh ra (gói Pro)
    note: str


FLOW_MODELS: Dict[str, FlowModel] = {
    "veo-3.1-fast": FlowModel(
        key="veo-3.1-fast",
        label="Veo 3.1 – Fast",
        credits=20,
        note="Rẻ nhất trong nhóm 3.1, đủ nét cho clip cử chỉ và video bán hàng dọc. NÊN DÙNG.",
    ),
    "veo-3.1-quality": FlowModel(
        key="veo-3.1-quality",
        label="Veo 3.1 – Quality",
        credits=100,
        note="Nét nhất nhưng đắt gấp 5 lần. Chỉ dùng cho 1-2 video chủ lực.",
    ),
    "veo-3.1-lite": FlowModel(
        key="veo-3.1-lite",
        label="Veo 3.1 – Lite",
        credits=10,
        note="Rẻ nhất nếu tài khoản của bạn có lựa chọn này (không phải tài khoản nào cũng có).",
    ),
}

DEFAULT_FLOW_MODEL = "veo-3.1-fast"

# Tạo ảnh chân dung nhân vật trong Flow (Nano Banana Pro) — ước tính, chỉnh được trong .env.
FLOW_IMAGE_CREDITS = 10

# Nâng lên 1080p/4K trong Flow tốn thêm tín dụng. Mặc định KHÔNG nâng.
FLOW_UPSCALE_CREDITS = 20


def credits_for(model_key: str, override: int = 0) -> int:
    """Tín dụng cho một lần tạo video. `override` > 0 thì dùng luôn giá tự nhập."""
    if override and override > 0:
        return int(override)
    model = FLOW_MODELS.get(model_key)
    return model.credits if model else FLOW_MODELS[DEFAULT_FLOW_MODEL].credits


def model_label(model_key: str) -> str:
    model = FLOW_MODELS.get(model_key)
    return model.label if model else model_key


# ---------------------------------------------------------------------------
# 9 cue cử chỉ của LiveFlow -> hành động mô tả cho Flow
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CueSpec:
    cue: str
    vi_label: str
    action: str  # câu ACTION tiếng Anh đưa vào prompt
    duration: int = 8


AVATAR_CUES: Tuple[CueSpec, ...] = (
    CueSpec(
        "WAVE_GREET",
        "Vẫy tay chào người xem",
        "She waves at the camera with a bright welcoming smile, then lowers her hand naturally.",
    ),
    CueSpec(
        "HOLD_PRODUCT",
        "Cầm sản phẩm giơ ngang ngực",
        "She holds an imaginary small product box up at chest height with both hands, presenting it toward the camera.",
    ),
    CueSpec(
        "SHOW_DETAIL",
        "Chỉ vào chi tiết sản phẩm",
        "She points with her index finger at a detail on the item she is holding at chest height, explaining it to the camera.",
    ),
    CueSpec(
        "POINT_PRICE",
        "Chỉ lên góc trên phải (bảng giá)",
        "She points toward the upper right corner of the frame with an excited, delighted expression.",
    ),
    CueSpec(
        "TRY_ON",
        "Thao tác dùng thử sản phẩm",
        "She demonstrates using the item in her hands naturally, turning it to show both sides.",
    ),
    CueSpec(
        "CTA_POINT_DOWN",
        "Chỉ xuống góc dưới trái (giỏ hàng)",
        "She points down toward the bottom left corner of the frame repeatedly, urging viewers to tap.",
    ),
    CueSpec(
        "NOD_LISTEN",
        "Gật đầu lắng nghe bình luận",
        "She nods slowly and smiles while listening attentively toward the camera, hands relaxed.",
    ),
    CueSpec(
        "SURPRISE",
        "Ngạc nhiên thích thú",
        "Her eyes widen with delighted surprise, both hands raised slightly near her chest.",
    ),
    CueSpec(
        "IDLE_TALK",
        "Đứng thuyết minh (clip loop chính)",
        "She talks naturally to the camera with relaxed hand gestures, blinking and shifting her weight slightly.",
    ),
)

# Clip nền (loop) phát suốt phiên live khi người mẫu không làm cử chỉ gì đặc biệt.
BASE_LOOP_CUE = "IDLE_TALK"


# ---------------------------------------------------------------------------
# Khuôn prompt
# ---------------------------------------------------------------------------
APPEARANCE_FEMALE = (
    "A friendly Vietnamese female livestream host in her mid-20s, long straight black hair, "
    "light natural makeup, wearing a clean smart-casual pastel blouse"
)
APPEARANCE_MALE = (
    "A friendly Vietnamese male livestream host in his late-20s, short neat black hair, "
    "wearing a clean smart-casual light shirt"
)

GREEN_BACKGROUND = (
    "a completely flat, uniform chroma key green screen (pure #00FF00), evenly lit, "
    "no shadows falling on the background, no props"
)

STUDIO_BACKGROUND = (
    "a bright modern livestream studio corner with soft bokeh lights and a shelf of products, "
    "shallow depth of field"
)

NEGATIVE_LINE = (
    "No camera movement, no zoom, no cuts, no transitions, no on-screen text, no captions, "
    "no subtitles, no watermark, no logos, no other people, subject stays fully inside the frame."
)


def build_avatar_prompt(
    action: str,
    *,
    appearance: str,
    duration: int = 8,
    green_screen: bool = True,
    outfit_note: str = "",
    speech_vi: str = "",
) -> str:
    """Prompt cho một clip người mẫu (cử chỉ hoặc loop nền).

    Giữ nguyên khối SUBJECT giữa mọi clip là cách duy nhất khiến mặt người mẫu không
    đổi giữa các clip — kết hợp thêm ảnh tham chiếu ở "Ingredients" trong Flow.
    """
    subject = appearance.strip().rstrip(".")
    if outfit_note.strip():
        subject += f", {outfit_note.strip().rstrip('.')}"

    background = GREEN_BACKGROUND if green_screen else STUDIO_BACKGROUND

    lines = [
        f"Vertical 9:16 video, {duration} seconds, photorealistic, 4K, cinematic soft studio lighting.",
        f"SUBJECT: {subject}.",
        f"ACTION: {action.strip()}",
        f"BACKGROUND: {background}.",
        "CAMERA: static locked-off medium shot framed from the knees up, subject centered.",
    ]
    if speech_vi.strip():
        lines.append(
            'DIALOGUE: she speaks to the camera in Vietnamese, clearly and energetically: '
            f'"{speech_vi.strip()}"'
        )
        lines.append("AUDIO: her voice only, no music, no background noise.")
    else:
        lines.append("AUDIO: ambient room tone only. She does not speak, her lips stay closed.")
    lines.append(f"NEGATIVE: {NEGATIVE_LINE}")
    return "\n".join(lines)


# Hậu tố ép khung dọc / không chữ cho prompt do AI viết (kịch bản sản phẩm).
PRODUCT_PROMPT_SUFFIX = (
    "Vertical 9:16 framing, photorealistic 4K, static locked-off camera. "
    f"NEGATIVE: {NEGATIVE_LINE}"
)


def flow_settings_hint(model_key: str, resolution: str, duration: int) -> str:
    """Bảng thiết lập cần chọn trong Flow — in vào gói việc để không phải nhớ."""
    return (
        "CÁCH ĐẶT TRONG FLOW (đặt một lần, dùng cho cả gói):\n"
        f"  • Model            : {model_label(model_key)}\n"
        f"  • Khung hình       : {FLOW_ASPECT_VERTICAL} (dọc / Portrait)\n"
        f"  • Độ phân giải     : {resolution}\n"
        f"  • Độ dài mỗi clip  : {duration} giây\n"
        "  • Outputs per prompt: 1   ← QUAN TRỌNG: để 2 là tốn gấp đôi tín dụng\n"
        "  • Ingredients      : tải ảnh chân dung người mẫu lên để mặt KHÔNG đổi giữa các clip\n"
        "  • Tải về           : chọn 'Download' bản gốc (không cần Upscale 4K — tốn thêm tín dụng)\n"
    )
