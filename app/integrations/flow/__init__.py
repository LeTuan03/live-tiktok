"""Google Flow (flow.google.com) — chế độ tạo video bằng tín dụng Flow của gói Google AI.

Flow **không có API công khai**. Vì vậy LiveFlow tự động hoá mọi thứ *quanh* Flow:
viết prompt, gom thành "gói việc", đếm tín dụng, canh thư mục Downloads, chuẩn hoá
video tải về rồi nạp thẳng vào thư viện / dự án. Người vận hành chỉ dán prompt và bấm
tạo trong Flow.

    pack = build_avatar_pack(...)      # 1. LiveFlow viết prompt + gói việc
    FlowDownloadWatcher(...)           # 2. canh file tải về
    import_job(pack, job, src)         # 3. chuẩn hoá + nạp vào LiveFlow
    CreditLedger().spend(...)          # 4. trừ tín dụng đã dùng
"""

from app.integrations.flow.constants import (
    AVATAR_CUES,
    FLOW_IMAGE_CREDITS,
    FLOW_MODELS,
    FLOW_URL,
    DEFAULT_FLOW_MODEL,
    credits_for,
    flow_settings_hint,
)
from app.integrations.flow.credits import CreditLedger
from app.integrations.flow.importer import (
    ClipInspection,
    ImportResult,
    find_tools,
    import_job,
    inspect_clip,
)
from app.integrations.flow.pack import (
    FlowJob,
    FlowPack,
    build_avatar_pack,
    build_product_pack,
    load_pack,
)
from app.integrations.flow.watcher import FlowDownloadWatcher, default_download_dirs

__all__ = [
    "AVATAR_CUES",
    "FLOW_IMAGE_CREDITS",
    "FLOW_MODELS",
    "FLOW_URL",
    "DEFAULT_FLOW_MODEL",
    "credits_for",
    "flow_settings_hint",
    "CreditLedger",
    "ClipInspection",
    "ImportResult",
    "find_tools",
    "import_job",
    "inspect_clip",
    "FlowJob",
    "FlowPack",
    "build_avatar_pack",
    "build_product_pack",
    "load_pack",
    "FlowDownloadWatcher",
    "default_download_dirs",
]
