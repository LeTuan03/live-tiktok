"""Comment Assistant — classify viewer comments and suggest grounded replies.

Grounding rules (spec §5.8 / §8):
* A suggested reply may only state facts present in the product record or the
  shop policies the operator configured. Anything else is marked
  ``needs_data`` and phrased as "we will check" instead of a promise.
* No claims about health, finance, product efficacy, certifications, stock or
  promotions that are not in the data.
* Nothing is ever reported as *sent* unless the platform confirmed it or the
  operator explicitly marked it as sent manually.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Sequence

from loguru import logger

from app.core.exceptions import TikTokCapabilityUnsupportedError
from app.database.models.comment import AISuggestion, CommentEvent
from app.database.models.product import Product
from app.database.repositories.comment_repo import CommentRepository
from app.integrations.llm.base import BaseLLMProvider
from app.services.platform_service import PlatformService

CATEGORY_LABELS: Dict[str, str] = {
    "price": "💰 Hỏi giá",
    "feature": "🔎 Hỏi tính năng",
    "shipping": "🚚 Vận chuyển",
    "variant": "📐 Biến thể/Size",
    "warranty": "🛡️ Bảo hành/Đổi trả",
    "spam": "🚫 Spam",
    "inappropriate": "⚠️ Không phù hợp",
    "other": "💬 Khác",
}

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("inappropriate", re.compile(r"\b(đm|dm|đmm|vcl|clm|cc|địt|đĩ|lừa đảo|scam|fake)\b", re.IGNORECASE)),
    ("spam", re.compile(r"(https?://|www\.|zalo\s*\d|kết bạn|follow\s*lại|fl\s*lại|inbox\s*mình|ib\s*mình)", re.IGNORECASE)),
    ("warranty", re.compile(r"(bảo hành|đổi trả|hoàn tiền|trả hàng|lỗi thì|hỏng)", re.IGNORECASE)),
    ("price", re.compile(r"(bao nhiêu|bao nhieu|nhiêu tiền|giá|bn tiền|bnt|mấy tiền|đắt|rẻ|giảm giá|voucher|mã giảm|sale)", re.IGNORECASE)),
    ("shipping", re.compile(r"(ship|vận chuyển|giao hàng|bao lâu|mấy ngày|phí giao|freeship|free ship|cod|nhận hàng)", re.IGNORECASE)),
    ("variant", re.compile(r"(size|màu|mau|cỡ|kích|dung tích|loại nào|phân loại|còn\s+\w+\s+không|hết hàng|còn hàng)", re.IGNORECASE)),
    ("feature", re.compile(r"(chất liệu|thành phần|dùng được|có .*không|công dụng|tác dụng|cách dùng|hướng dẫn|chính hãng|auth|thật không|xài|hiệu quả|tốt không)", re.IGNORECASE)),
]

# Phrases the AI must never produce (unverifiable commitments).
_FORBIDDEN = re.compile(
    r"(chữa|trị dứt|khỏi hẳn|100%|cam kết hiệu quả|đảm bảo (giảm|tăng|trắng|hết)|"
    r"không hiệu quả hoàn tiền|freeship toàn quốc|chính hãng 100%|bộ y tế|fda|chứng nhận)",
    re.IGNORECASE,
)


def _fmt_price(value: Optional[float]) -> str:
    return f"{value:,.0f}đ".replace(",", ".") if value else ""


class CommentService:
    """Service layer for comment processing and grounded reply suggestions."""

    def __init__(
        self,
        comment_repo: CommentRepository,
        platform_service: Optional[PlatformService] = None,
    ) -> None:
        self.comment_repo = comment_repo
        self.platform_service = platform_service or PlatformService()
        # Auto-reply is OFF by default and cannot be enabled while SEND_COMMENT is unsupported.
        self.auto_reply_enabled: bool = False

    # ---------------------------------------------------------- classify
    @staticmethod
    def classify(text: str) -> str:
        for category, pattern in _PATTERNS:
            if pattern.search(text):
                return category
        return "other"

    # --------------------------------------------------- template replies
    def build_grounded_reply(
        self,
        text: str,
        category: str,
        product: Optional[Product],
        policies: Optional[Dict[str, str]] = None,
        honorific: str = "bạn",
    ) -> tuple[str, bool, str]:
        """Return (reply, needs_data, grounding_note) using ONLY known data."""
        policies = policies or {}
        name = product.name if product else "sản phẩm"

        if category in ("spam", "inappropriate"):
            return ("", False, "Không trả lời bình luận spam/không phù hợp.")

        if category == "price":
            if product and (product.promo_price or product.price):
                if product.promo_price and product.price and product.promo_price < product.price:
                    reply = (
                        f"Dạ {honorific}, {name} đang có giá ưu đãi {_fmt_price(product.promo_price)} "
                        f"(giá gốc {_fmt_price(product.price)}) ạ. {honorific.capitalize()} xem chi tiết trong giỏ hàng nhé!"
                    )
                else:
                    price = product.promo_price or product.price
                    reply = f"Dạ {honorific}, {name} có giá {_fmt_price(price)} ạ. {honorific.capitalize()} xem thêm trong giỏ hàng nhé!"
                return reply, False, "Giá lấy từ dữ liệu sản phẩm."
            return (
                f"Dạ {honorific} chờ em một chút, em kiểm tra giá chính xác của {name} rồi báo lại ngay ạ!",
                True,
                "Thiếu dữ liệu giá trong hồ sơ sản phẩm.",
            )

        if category == "shipping":
            policy = policies.get("shipping", "").strip()
            if policy:
                return f"Dạ {honorific}, về giao hàng: {policy}", False, "Theo chính sách vận chuyển đã cấu hình."
            return (
                f"Dạ {honorific}, thông tin phí và thời gian giao hàng hiển thị ngay khi {honorific} bấm vào giỏ hàng ạ, "
                "em kiểm tra thêm và báo lại ngay!",
                True,
                "Chưa cấu hình chính sách vận chuyển (Cài đặt → Thông tin shop).",
            )

        if category == "warranty":
            policy = (policies.get("warranty", "") or policies.get("return", "")).strip()
            if policy:
                return f"Dạ {honorific}, về bảo hành/đổi trả: {policy}", False, "Theo chính sách đã cấu hình."
            return (
                f"Dạ {honorific} đợi em xíu, em xác nhận chính sách bảo hành/đổi trả của {name} rồi trả lời ngay ạ!",
                True,
                "Chưa cấu hình chính sách bảo hành/đổi trả.",
            )

        if category == "variant":
            if product and product.sizes:
                return (
                    f"Dạ {honorific}, {name} hiện có các phân loại: {product.sizes}. {honorific.capitalize()} chọn trong giỏ hàng nhé!",
                    False,
                    "Phân loại lấy từ dữ liệu sản phẩm.",
                )
            return (
                f"Dạ {honorific} chờ em kiểm tra phân loại/size còn hàng của {name} rồi báo lại ngay ạ!",
                True,
                "Thiếu dữ liệu biến thể/size.",
            )

        if category == "feature":
            facts = []
            if product:
                if product.verified_features:
                    facts.append(product.verified_features.replace("\n", "; ").strip("; "))
                if product.ingredients:
                    facts.append(f"chất liệu/thành phần: {product.ingredients}")
                if product.usage_instructions:
                    facts.append(f"cách dùng: {product.usage_instructions}")
            if facts:
                summary = "; ".join(facts)[:280]
                return (
                    f"Dạ {honorific}, {name} có các đặc điểm đã xác minh: {summary}.",
                    False,
                    "Chỉ dùng tính năng đã xác minh trong hồ sơ sản phẩm.",
                )
            return (
                f"Dạ {honorific} chờ em một chút, em kiểm tra thông tin chi tiết của {name} rồi trả lời ngay ạ!",
                True,
                "Thiếu dữ liệu tính năng đã xác minh.",
            )

        return (
            f"Dạ cảm ơn {honorific} đã quan tâm! {honorific.capitalize()} cần em tư vấn thêm gì về {name} cứ hỏi em nhé!",
            False,
            "Câu trả lời chung, không đưa cam kết.",
        )

    # ------------------------------------------------------------ process
    def process_comment(
        self,
        username: str,
        comment_text: str,
        product: Optional[Product] = None,
        policies: Optional[Dict[str, str]] = None,
        honorific: str = "bạn",
        session_id: Optional[int] = None,
        source: str = "manual",
    ) -> tuple[CommentEvent, Optional[AISuggestion]]:
        """Classify, store the comment and create a grounded template suggestion."""
        text = comment_text.strip()
        category = self.classify(text)
        is_spam = category in ("spam", "inappropriate")

        event = self.comment_repo.create_comment_event(
            username=username.strip() or "Khách",
            content=text,
            category=category,
            session_id=session_id,
            product_id=product.id if product else None,
            source=source,
            is_spam=is_spam,
        )

        reply, needs_data, note = self.build_grounded_reply(text, category, product, policies, honorific)
        suggestion = None
        if reply:
            suggestion = self.comment_repo.create_suggestion(
                comment_id=event.id,
                suggested_reply=reply,
                confidence=0.6 if needs_data else 0.85,
                is_auto_reply=False,
                product_id=product.id if product else None,
                needs_data=needs_data,
                grounding_note=note,
            )
        return event, suggestion

    # ----------------------------------------------------------- with AI
    async def suggest_with_ai(
        self,
        comment_text: str,
        category: str,
        product: Optional[Product],
        provider: BaseLLMProvider,
        policies: Optional[Dict[str, str]] = None,
        honorific: str = "bạn",
    ) -> Dict[str, Any]:
        """Ask the LLM for a short grounded reply. Returns {reply, needs_data, note}."""
        policies = policies or {}
        product_block = "KHÔNG CÓ DỮ LIỆU SẢN PHẨM."
        if product:
            fields = {
                "Tên": product.name,
                "Giá gốc": _fmt_price(product.price),
                "Giá khuyến mãi": _fmt_price(product.promo_price),
                "Mô tả": product.description or "",
                "Tính năng đã xác minh": product.verified_features or "",
                "Thành phần/chất liệu": product.ingredients or "",
                "Phân loại/size": product.sizes or "",
                "Đối tượng": product.target_audience or "",
                "Hướng dẫn sử dụng": product.usage_instructions or "",
                "Lưu ý": product.warnings or "",
            }
            product_block = "\n".join(f"- {k}: {v}" for k, v in fields.items() if v)

        policy_block = "\n".join(
            f"- {k}: {v}" for k, v in {
                "Vận chuyển": policies.get("shipping", ""),
                "Đổi trả": policies.get("return", ""),
                "Bảo hành": policies.get("warranty", ""),
            }.items() if v
        ) or "KHÔNG CÓ CHÍNH SÁCH ĐƯỢC CẤU HÌNH."

        system_prompt = (
            "Bạn là trợ lý trả lời bình luận cho người bán hàng livestream. "
            "QUY TẮC TUYỆT ĐỐI:\n"
            "1. Chỉ dùng thông tin trong DỮ LIỆU SẢN PHẨM và CHÍNH SÁCH SHOP bên dưới. Không bịa giá, ưu đãi, tồn kho, "
            "chứng nhận, công dụng, hiệu quả, chính sách.\n"
            "2. Nếu không có dữ liệu để trả lời, hãy nói sẽ kiểm tra và phản hồi lại (needs_data=true).\n"
            "3. Không hứa hẹn về sức khỏe, tài chính hay hiệu quả sản phẩm.\n"
            "4. Trả lời ngắn (tối đa 45 từ), tiếng Việt tự nhiên, lịch sự, xưng 'em' và gọi khách là "
            f"'{honorific}'.\n"
            "5. Trả về DUY NHẤT một JSON: {\"reply\": \"...\", \"needs_data\": true/false, \"note\": \"căn cứ ngắn\"}"
        )
        user_prompt = (
            f"PHÂN LOẠI: {CATEGORY_LABELS.get(category, category)}\n"
            f"BÌNH LUẬN CỦA KHÁCH: \"{comment_text}\"\n\n"
            f"DỮ LIỆU SẢN PHẨM:\n{product_block}\n\n"
            f"CHÍNH SÁCH SHOP:\n{policy_block}"
        )

        raw = await provider.generate_text(user_prompt, system_prompt=system_prompt, temperature=0.3, max_tokens=300)
        clean = raw.strip()
        match = re.search(r"\{.*\}", clean, re.DOTALL)
        data: Dict[str, Any]
        try:
            data = json.loads(match.group(0) if match else clean)
        except Exception:
            data = {"reply": clean[:300], "needs_data": True, "note": "AI không trả về JSON hợp lệ."}

        reply = str(data.get("reply", "")).strip()
        needs_data = bool(data.get("needs_data", False))
        note = str(data.get("note", "")).strip()

        if _FORBIDDEN.search(reply):
            logger.warning("AI reply chứa cam kết không có căn cứ, đã chặn: {}", reply)
            reply, needs_data, note = self.build_grounded_reply(comment_text, category, product, policies, honorific)
            note = "Đã thay bằng mẫu an toàn vì AI đưa cam kết không có căn cứ. " + note

        return {"reply": reply, "needs_data": needs_data, "note": note}

    def attach_ai_suggestion(
        self, comment_id: int, data: Dict[str, Any], product_id: Optional[int] = None
    ) -> AISuggestion:
        return self.comment_repo.create_suggestion(
            comment_id=comment_id,
            suggested_reply=data.get("reply", ""),
            confidence=0.5 if data.get("needs_data") else 0.8,
            product_id=product_id,
            needs_data=bool(data.get("needs_data")),
            grounding_note=f"AI: {data.get('note', '')}",
        )

    # ----------------------------------------------------------- workflow
    def approve(self, suggestion_id: int, edited_reply: Optional[str] = None, by: str = "operator") -> Optional[AISuggestion]:
        return self.comment_repo.update_suggestion(
            suggestion_id, status="approved", suggested_reply=edited_reply, approved_by=by
        )

    def reject(self, suggestion_id: int) -> Optional[AISuggestion]:
        return self.comment_repo.update_suggestion(suggestion_id, status="rejected")

    def send_reply(self, suggestion_id: int) -> AISuggestion:
        """Attempt automated sending. Raises when the platform does not support it.

        The suggestion status is only set to ``sent`` on platform confirmation,
        which no supported platform provides today; otherwise it stays
        ``approved`` and the error is recorded.
        """
        suggestion = self.comment_repo.get_suggestion(suggestion_id)
        if not suggestion:
            raise ValueError("Không tìm thấy gợi ý trả lời.")
        try:
            confirmed = self.platform_service.send_comment(suggestion.suggested_reply)
        except TikTokCapabilityUnsupportedError as e:
            self.comment_repo.update_suggestion(suggestion_id, send_error=str(e))
            raise
        if confirmed:  # pragma: no cover — no supported platform yet
            return self.comment_repo.update_suggestion(
                suggestion_id, status="sent", sent_at=datetime.now(timezone.utc)
            )  # type: ignore[return-value]
        self.comment_repo.update_suggestion(suggestion_id, send_error="Nền tảng không xác nhận gửi.")
        return suggestion

    def mark_sent_manually(self, suggestion_id: int, by: str = "operator") -> Optional[AISuggestion]:
        """Operator confirms they pasted the reply on the platform themselves."""
        return self.comment_repo.update_suggestion(
            suggestion_id,
            status="sent_manual",
            approved_by=by,
            sent_at=datetime.now(timezone.utc),
        )

    def set_auto_reply(self, enabled: bool) -> bool:
        """Auto-reply can only be enabled when sending is actually supported."""
        from app.integrations.tiktok.capabilities import Capability, is_supported

        if enabled and not is_supported(self.platform_service.platform, Capability.SEND_COMMENT):
            self.auto_reply_enabled = False
            return False
        self.auto_reply_enabled = enabled
        return self.auto_reply_enabled

    def get_recent_comments(self, limit: int = 100) -> Sequence[CommentEvent]:
        return self.comment_repo.list_recent_comments(limit)

    def clear_history(self) -> int:
        return self.comment_repo.delete_all()
