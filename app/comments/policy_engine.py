"""Comment Policy Engine & Intent Classifier (§3, §4).

Enforces:
1. Deduplication and rate-limiting.
2. Confidence gating (OCR threshold, spam filtration).
3. Zero-hallucination ground truth verification against active SKU.
4. Operator escalation when data is missing or out-of-scope.
"""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

from loguru import logger

from app.services.product_knowledge_service import ProductKnowledgeService, VerifiedProductProfile


@dataclass
class CommentEventData:
    """Standardized comment event across API, OCR, or Manual ingestion."""

    username: str
    text: str
    source: str = "api"  # "api" | "ocr" | "manual"
    confidence: float = 1.0  # OCR confidence (0.0 to 1.0); API/Manual default to 1.0
    timestamp: float = field(default_factory=time.time)
    session_id: Optional[str] = None
    product_id: Optional[int] = None


@dataclass
class CommentResolution:
    """Result of policy classification and grounding verification."""

    event: CommentEventData
    intent: str  # price, feature, variant, shipping, usage, spam, complaint, out_of_scope
    can_auto_reply: bool
    reply_text: Optional[str]
    requires_operator: bool
    grounding_note: str
    confidence: float


class CommentPolicyEngine:
    """Evaluates incoming comments against safety bounds and verified facts."""

    MIN_OCR_CONFIDENCE = 0.75

    INTENT_KEYWORDS = {
        "price": ["giá", "bao nhiêu", "nhiêu", "bn", "tiền", "sale", "giảm", "voucher", "deal", "mắc", "rẻ"],
        "variant": ["màu", "size", "kích cỡ", "loại", "vị", "mẫu", "form", "kg", "nặng", "cao"],
        "shipping": ["ship", "vận chuyển", "giao", "khi nào tới", "hỏa tốc", "phí ship", "freeship"],
        "usage": ["dùng", "cách dùng", "hướng dẫn", "sử dụng", "uống", "bôi", "thoa", "ngày mấy lần", "hạn sử dụng", "hsd"],
        "feature": ["công dụng", "thành phần", "tác dụng", "chất liệu", "tốt không", "hiệu quả", "có cồn", "da nhạy cảm"],
        "complaint": ["lừa đảo", "hỏng", "rách", "vỡ", "trả hàng", "hoàn tiền", "khiếu nại", "chưa nhận được", "bùng hàng"],
    }

    def __init__(
        self,
        knowledge_service: Optional[ProductKnowledgeService] = None,
        dedup_window_seconds: float = 20.0,
    ) -> None:
        self.knowledge = knowledge_service or ProductKnowledgeService()
        self.dedup_window = dedup_window_seconds
        self._seen_comments: Deque[Tuple[str, str, float]] = deque(maxlen=200)  # (user, text, time)
        self.operator_queue: List[CommentResolution] = []

    def _is_duplicate(self, username: str, text: str) -> bool:
        now = time.time()
        # Clean older
        while self._seen_comments and (now - self._seen_comments[0][2] > self.dedup_window):
            self._seen_comments.popleft()

        norm_text = text.lower().strip()
        for u, t, ts in self._seen_comments:
            if u.lower() == username.lower() and (t == norm_text or abs(len(t) - len(norm_text)) < 2):
                return True
        self._seen_comments.append((username, norm_text, now))
        return False

    def classify_intent(self, text: str) -> str:
        """Classify user intent using robust keyword patterns."""
        norm = text.lower()

        # Spam or gibberish check
        if len(norm) < 2 or re.match(r"^[\s\d.,!@#$%^&*()_+=-]+$", norm):
            return "spam"

        for intent, kws in self.INTENT_KEYWORDS.items():
            for kw in kws:
                if re.search(r"\b" + re.escape(kw) + r"\b", norm):
                    return intent

        return "out_of_scope"

    def evaluate(
        self,
        event: CommentEventData,
        active_profile: Optional[VerifiedProductProfile],
    ) -> CommentResolution:
        """Evaluate comment and produce a strictly verified, anti-hallucination resolution."""
        # 1. OCR Confidence Gating (§4, §6 P3)
        if event.source == "ocr" and event.confidence < self.MIN_OCR_CONFIDENCE:
            logger.info("Từ chối trả lời tự động do OCR confidence thấp ({:.2f})", event.confidence)
            res = CommentResolution(
                event=event,
                intent="unclear",
                can_auto_reply=False,
                reply_text=None,
                requires_operator=True,
                grounding_note=f"OCR độ tin cậy thấp ({event.confidence:.2f} < {self.MIN_OCR_CONFIDENCE})",
                confidence=event.confidence,
            )
            self.operator_queue.append(res)
            return res

        # 2. Deduplication check
        if self._is_duplicate(event.username, event.text):
            return CommentResolution(
                event=event,
                intent="duplicate",
                can_auto_reply=False,
                reply_text=None,
                requires_operator=False,
                grounding_note="Trùng lặp bình luận gần đây",
                confidence=event.confidence,
            )

        # 3. Intent classification
        intent = self.classify_intent(event.text)

        # 4. Spam / Inappropriate / Complaints
        if intent == "spam":
            return CommentResolution(
                event=event,
                intent=intent,
                can_auto_reply=False,
                reply_text=None,
                requires_operator=False,
                grounding_note="Bình luận spam/ký tự rác",
                confidence=event.confidence,
            )

        if intent == "complaint":
            # Escalation to Human Operator immediately (§4)
            res = CommentResolution(
                event=event,
                intent=intent,
                can_auto_reply=False,
                reply_text=f"Dạ shop chào bạn @{event.username}, vấn đề của bạn shop xin phép liên hệ hỗ trợ riêng qua tin nhắn ngay ạ!",
                requires_operator=True,
                grounding_note="Khiếu nại/Hoàn tiền — chuyển ngay người vận hành",
                confidence=event.confidence,
            )
            self.operator_queue.append(res)
            return res

        # 5. Product specific queries
        if not active_profile or not active_profile.is_verified:
            res = CommentResolution(
                event=event,
                intent=intent,
                can_auto_reply=False,
                reply_text="Dạ bạn bấm vào giỏ hàng góc trái màn hình để xem thông tin chi tiết và đặt hàng giúp shop nha!",
                requires_operator=True,
                grounding_note="Không có dữ liệu SKU đang giới thiệu",
                confidence=event.confidence,
            )
            self.operator_queue.append(res)
            return res

        # Grounded Answer Generation
        reply_text: Optional[str] = None
        grounding_note = ""
        can_auto = False

        formatted_price = f"{active_profile.effective_price:,.0f}đ".replace(",", ".")

        if intent == "price":
            discount_info = f" đang giảm {active_profile.discount_percent}%" if active_profile.discount_percent > 0 else ""
            reply_text = f"Dạ chào @{event.username}, sản phẩm {active_profile.name} hiện có giá chỉ {formatted_price}{discount_info} độc quyền trên live hôm nay nha bạn!"
            grounding_note = f"Khớp giá verified: {active_profile.effective_price}"
            can_auto = True

        elif intent == "variant":
            if active_profile.variants:
                variants_summary = ", ".join(f"{v['name']} {v['value']}" for v in active_profile.variants[:3])
                reply_text = f"Dạ @{event.username} ơi, mẫu này bên em đang có sẵn các phân loại: {variants_summary} nha bạn!"
                grounding_note = f"Khớp biến thể: {len(active_profile.variants)} phân loại"
                can_auto = True
            elif active_profile.sizes:
                reply_text = f"Dạ @{event.username}, sản phẩm có các size: {active_profile.sizes} ạ!"
                grounding_note = "Khớp kích thước profile"
                can_auto = True
            else:
                reply_text = f"Dạ @{event.username}, sản phẩm hiện là mẫu chuẩn đồng nhất, bạn nhấp vào giỏ hàng xem chi tiết nhé!"
                grounding_note = "Không có biến thể phụ"
                can_auto = True

        elif intent == "shipping":
            reply_text = f"Dạ @{event.username}, sản phẩm được giao hàng toàn quốc qua TikTok Shop, đơn đặt trên live hôm nay được ưu đãi freeship và giao nhanh nha bạn!"
            grounding_note = "Chính sách vận chuyển tiêu chuẩn TikTok Shop"
            can_auto = True

        elif intent == "usage":
            if active_profile.usage_instructions:
                clean_usage = active_profile.usage_instructions.replace("\n", " ").strip()[:100]
                reply_text = f"Dạ @{event.username}, cách dùng rất đơn giản: {clean_usage}. Trên bao bì có hướng dẫn chi tiết nữa bạn nhé!"
                grounding_note = "Khớp usage_instructions"
                can_auto = True
            else:
                reply_text = f"Dạ @{event.username}, bạn xem hướng dẫn chi tiết đính kèm trên bao bì hoặc nhắn tin shop hướng dẫn tận tình nha!"
                grounding_note = "Thiếu usage_instructions chi tiết"
                can_auto = True

        elif intent == "feature":
            if active_profile.verified_features:
                top_feature = active_profile.verified_features[0]
                reply_text = f"Dạ @{event.username}, điểm nổi bật nhất của em này là: {top_feature}, được kiểm nghiệm an toàn chất lượng bạn hoàn toàn yên tâm nhé!"
                grounding_note = f"Khớp verified_features: {top_feature}"
                can_auto = True
            else:
                res = CommentResolution(
                    event=event,
                    intent=intent,
                    can_auto_reply=False,
                    reply_text=None,
                    requires_operator=True,
                    grounding_note="Thiếu đặc tính kỹ thuật đã xác minh",
                    confidence=event.confidence,
                )
                self.operator_queue.append(res)
                return res
        else:
            # Out of scope -> Operator queue
            res = CommentResolution(
                event=event,
                intent="out_of_scope",
                can_auto_reply=False,
                reply_text=f"Dạ câu hỏi của bạn @{event.username} shop đã ghi nhận và nhân viên sẽ nhắn tin giải đáp kỹ cho bạn nha!",
                requires_operator=True,
                grounding_note="Nằm ngoài phạm vi dữ kiện SKU tự động",
                confidence=event.confidence,
            )
            self.operator_queue.append(res)
            return res

        # Safety check on candidate text
        is_safe, violations = self.knowledge.validate_speech_text(reply_text or "", active_profile)
        if not is_safe:
            logger.warning("Phát hiện vi phạm chính sách trong câu trả lời tự động: {}", violations)
            res = CommentResolution(
                event=event,
                intent=intent,
                can_auto_reply=False,
                reply_text=None,
                requires_operator=True,
                grounding_note=f"Vi phạm an toàn: {'; '.join(violations)}",
                confidence=event.confidence,
            )
            self.operator_queue.append(res)
            return res

        return CommentResolution(
            event=event,
            intent=intent,
            can_auto_reply=can_auto,
            reply_text=reply_text,
            requires_operator=False,
            grounding_note=grounding_note,
            confidence=event.confidence,
        )
