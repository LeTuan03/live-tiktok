"""Capability-based platform integration (TikTok LIVE, Shopee Live).

Every platform feature carries an explicit status so the UI never implies
that something works when it does not. The app streams through OBS (RTMP),
which both platforms officially support for eligible sellers; everything
that would require a private/undocumented API is exposed as *Manual Mode*
with step-by-step instructions instead.

Rules (spec §6):
* Never fabricate endpoints or simulate success.
* Never store platform passwords; the user logs in on the official app/site.
* No scraping of private APIs, no CAPTCHA/OTP bypass, no browser automation
  that impersonates the user.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List


class Platform(str, Enum):
    TIKTOK = "tiktok"
    SHOPEE = "shopee"

    @property
    def display_name(self) -> str:
        return {"tiktok": "TikTok LIVE", "shopee": "Shopee Live"}[self.value]


class CapabilityStatus(str, Enum):
    SUPPORTED = "supported"  # verified & implemented through an allowed method
    UNSUPPORTED = "unsupported"  # no legitimate integration path available
    REQUIRES_AUTHORIZATION = "requires_authorization"  # official API exists but needs approval
    MANUAL = "manual"  # user performs the action on the official UI; app assists
    DEMO = "demo"  # simulated data for testing only

    @property
    def label(self) -> str:
        return {
            "supported": "✅ Hỗ trợ",
            "unsupported": "⛔ Không hỗ trợ",
            "requires_authorization": "🔐 Cần quyền/API",
            "manual": "🖐️ Thủ công (có hướng dẫn)",
            "demo": "🧪 Demo",
        }[self.value]


class Capability(str, Enum):
    STREAM_VIDEO = "stream_video"  # push A/V to the platform
    READ_COMMENTS = "read_comments"
    SEND_COMMENT = "send_comment"
    PIN_PRODUCT = "pin_product"
    READ_ORDERS = "read_orders"
    START_LIVE_SESSION = "start_live_session"  # create/open the LIVE room

    @property
    def display_name(self) -> str:
        return {
            "stream_video": "Phát video/âm thanh lên LIVE",
            "read_comments": "Đọc bình luận thời gian thực",
            "send_comment": "Gửi bình luận/trả lời",
            "pin_product": "Ghim sản phẩm lên LIVE",
            "read_orders": "Đọc đơn hàng trong LIVE",
            "start_live_session": "Tạo/mở phòng LIVE",
        }[self.value]


@dataclass(frozen=True)
class CapabilityInfo:
    capability: Capability
    status: CapabilityStatus
    method: str  # how it is achieved (or why not)
    manual_steps: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------- matrices
_TIKTOK: Dict[Capability, CapabilityInfo] = {
    Capability.STREAM_VIDEO: CapabilityInfo(
        Capability.STREAM_VIDEO,
        CapabilityStatus.SUPPORTED,
        "OBS Studio phát RTMP tới TikTok bằng Server URL + Stream Key chính thức "
        "(TikTok LIVE Studio hoặc trang LIVE Center). LiveFlow điều phối nội dung trong OBS.",
        [
            "Mở TikTok LIVE Studio (PC) hoặc TikTok LIVE Center → 'Go LIVE' → chọn 'Phần mềm phát trực tiếp (OBS/Streamlabs)'.",
            "Sao chép Server URL và Stream Key (yêu cầu tài khoản đủ điều kiện LIVE trên PC; TikTok có thể đổi điều kiện theo thời gian).",
            "Dán vào LiveFlow → Live Studio → 'Cấu hình RTMP cho OBS' (hoặc OBS → Settings → Stream → Custom).",
            "Bấm 'Bắt đầu LIVE' trong LiveFlow (hoặc 'Start Streaming' trong OBS). Kiểm tra phòng LIVE trên TikTok.",
        ],
    ),
    Capability.START_LIVE_SESSION: CapabilityInfo(
        Capability.START_LIVE_SESSION,
        CapabilityStatus.MANUAL,
        "Phòng LIVE phải được mở trên ứng dụng/trang chính thức của TikTok bằng tài khoản của bạn.",
        ["Đăng nhập TikTok trên app hoặc LIVE Studio và mở phòng LIVE trước khi bấm Start Streaming."],
    ),
    Capability.READ_COMMENTS: CapabilityInfo(
        Capability.READ_COMMENTS,
        CapabilityStatus.MANUAL,
        "TikTok không cung cấp API công khai đọc bình luận LIVE cho ứng dụng bên thứ ba chưa được duyệt. "
        "Chế độ thủ công: dán/nhập bình luận, AI gợi ý trả lời.",
        [
            "Mở màn hình bình luận LIVE trên TikTok (LIVE Studio hiển thị chat ngay bên cạnh).",
            "Sao chép câu hỏi của khách và dán vào 'Trợ lý bình luận' để AI gợi ý trả lời có căn cứ.",
        ],
    ),
    Capability.SEND_COMMENT: CapabilityInfo(
        Capability.SEND_COMMENT,
        CapabilityStatus.MANUAL,
        "Không có API gửi bình luận hợp lệ cho bên thứ ba. Người vận hành tự gửi câu trả lời đã duyệt.",
        [
            "Bấm 'Sao chép' câu trả lời đã duyệt trong LiveFlow.",
            "Dán vào ô chat trên TikTok LIVE và gửi, hoặc bấm '🔊 Đọc trên LIVE' để AI đọc câu trả lời bằng giọng nói.",
            "Đánh dấu 'Đã gửi' trong LiveFlow để lưu nhật ký.",
        ],
    ),
    Capability.PIN_PRODUCT: CapabilityInfo(
        Capability.PIN_PRODUCT,
        CapabilityStatus.MANUAL,
        "Ghim sản phẩm chỉ thực hiện được trên giao diện TikTok Shop/LIVE chính thức.",
        [
            "Trong phòng LIVE → biểu tượng giỏ hàng → chọn sản phẩm → 'Ghim' (Pin).",
            "Trong LiveFlow, bấm 'Đánh dấu đã ghim' để theo dõi sản phẩm đang được ghim.",
        ],
    ),
    Capability.READ_ORDERS: CapabilityInfo(
        Capability.READ_ORDERS,
        CapabilityStatus.REQUIRES_AUTHORIZATION,
        "TikTok Shop Open API yêu cầu đăng ký ứng dụng đối tác và được duyệt; chưa tích hợp trong phiên bản này.",
        ["Theo dõi đơn trong TikTok Shop Seller Center."],
    ),
}

_SHOPEE: Dict[Capability, CapabilityInfo] = {
    Capability.STREAM_VIDEO: CapabilityInfo(
        Capability.STREAM_VIDEO,
        CapabilityStatus.SUPPORTED,
        "Shopee Live cho phép phát qua phần mềm bên thứ ba (OBS) bằng Stream URL + Stream Key "
        "lấy trong ứng dụng Shopee khi tạo phiên live (áp dụng cho shop đủ điều kiện).",
        [
            "Mở app Shopee → Shopee Live → 'Tạo phiên live' → chọn 'Phát qua phần mềm/Thiết bị khác (OBS)' nếu có.",
            "Sao chép Stream URL và Stream Key hiển thị trong app.",
            "Dán vào LiveFlow → Live Studio → 'Cấu hình RTMP cho OBS'.",
            "Bấm 'Bắt đầu LIVE' trong LiveFlow rồi xác nhận 'Bắt đầu' trong app Shopee.",
        ],
    ),
    Capability.START_LIVE_SESSION: CapabilityInfo(
        Capability.START_LIVE_SESSION,
        CapabilityStatus.MANUAL,
        "Phiên live được tạo và bắt đầu trong ứng dụng Shopee bằng tài khoản người bán.",
        ["Tạo phiên live trong app Shopee trước, sau đó phát từ OBS."],
    ),
    Capability.READ_COMMENTS: CapabilityInfo(
        Capability.READ_COMMENTS,
        CapabilityStatus.MANUAL,
        "Shopee Open Platform không cung cấp API bình luận live cho ứng dụng chưa được duyệt.",
        ["Theo dõi bình luận trên app Shopee Live, dán vào 'Trợ lý bình luận' để AI gợi ý trả lời."],
    ),
    Capability.SEND_COMMENT: CapabilityInfo(
        Capability.SEND_COMMENT,
        CapabilityStatus.MANUAL,
        "Người vận hành tự gửi câu trả lời đã duyệt trên app Shopee hoặc để AI đọc bằng giọng nói.",
        ["Sao chép câu trả lời → dán vào chat Shopee Live → đánh dấu 'Đã gửi'."],
    ),
    Capability.PIN_PRODUCT: CapabilityInfo(
        Capability.PIN_PRODUCT,
        CapabilityStatus.MANUAL,
        "Ghim/đẩy sản phẩm thực hiện trong app Shopee Live.",
        ["Trong phiên live → giỏ hàng → chọn sản phẩm → 'Ghim'. Đánh dấu trong LiveFlow để theo dõi."],
    ),
    Capability.READ_ORDERS: CapabilityInfo(
        Capability.READ_ORDERS,
        CapabilityStatus.REQUIRES_AUTHORIZATION,
        "Shopee Open Platform yêu cầu đăng ký đối tác và được duyệt; chưa tích hợp.",
        ["Theo dõi đơn trong Kênh Người Bán Shopee."],
    ),
}

_MATRICES: Dict[Platform, Dict[Capability, CapabilityInfo]] = {
    Platform.TIKTOK: _TIKTOK,
    Platform.SHOPEE: _SHOPEE,
}


def get_capabilities(platform: Platform | str) -> Dict[Capability, CapabilityInfo]:
    platform = Platform(platform)
    return dict(_MATRICES[platform])


def get_capability(platform: Platform | str, capability: Capability) -> CapabilityInfo:
    return get_capabilities(platform)[capability]


def is_supported(platform: Platform | str, capability: Capability) -> bool:
    return get_capability(platform, capability).status == CapabilityStatus.SUPPORTED


def rtmp_hint(platform: Platform | str) -> str:
    """Known default RTMP server patterns; the real value always comes from the platform UI."""
    platform = Platform(platform)
    if platform == Platform.TIKTOK:
        return "Lấy từ TikTok LIVE Studio / LIVE Center (dạng rtmp://...tiktokcdn.../...)."
    return "Lấy trong app Shopee khi tạo phiên live (dạng rtmp://...shopee.../...)."
