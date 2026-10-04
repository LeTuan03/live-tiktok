"""Grounded Comment Auto-Reply Service — strictly verifies facts before answering live viewers (§3.6, §6 P5)."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from app.comments.reply_clips import ReplyClipManager
from app.comments.sources.screen_ocr import ExtractedComment
from app.database.models.comment import CommentEvent
from app.database.models.product import Product
from app.database.repositories.comment_repo import CommentRepository
from app.playout.engine import PlayoutEngine
from app.services.comment_service import CommentService


class GroundedCommentReplyService:
    """Orchestrates screen OCR comments, verifies facts against product data, and triggers priority playout replies."""

    # Whitelist of categories permitted for autonomous live response
    AUTOREPLY_WHITELIST = {"price", "shipping", "warranty", "variant", "feature"}

    def __init__(
        self,
        comment_service: CommentService,
        reply_clip_manager: Optional[ReplyClipManager] = None,
        confidence_threshold: float = 0.85,
    ) -> None:
        self.comment_service = comment_service
        self.reply_clip_manager = reply_clip_manager or ReplyClipManager()
        self.confidence_threshold = confidence_threshold
        self.auto_reply_enabled: bool = True

    def evaluate_comment_for_reply(
        self,
        comment: ExtractedComment,
        active_product: Optional[Product],
        shop_policies: Optional[Dict[str, str]] = None,
    ) -> Tuple[bool, Optional[str], str]:
        """Evaluate whether a comment can be autonomously answered using strictly verified data.

        Returns:
            (can_auto_reply, reply_text, reason)
        """
        category = self.comment_service.classify(comment.text)

        # 1. Spam and inappropriate comments are instantly rejected
        if category in ("spam", "inappropriate"):
            return False, None, f"Bình luận bị chặn do thuộc loại: {category}"

        # 2. Check category against whitelist
        if category not in self.AUTOREPLY_WHITELIST:
            return False, None, f"Thể loại '{category}' chưa nằm trong danh sách trắng tự động trả lời"

        # 3. Build grounded reply
        reply, needs_data, grounding_note = self.comment_service.build_grounded_reply(
            text=comment.text,
            category=category,
            product=active_product,
            policies=shop_policies,
            honorific="bạn",
        )

        # 4. Strict grounding check: if missing data, NEVER hallucinate (§6 P5 acceptance criteria)
        if needs_data:
            logger.info("Từ chối trả lời tự động cho @{}: Thiếu dữ liệu xác thực ({})", comment.username, grounding_note)
            return False, None, f"Thiếu dữ liệu xác minh: {grounding_note}"

        if not reply or len(reply.strip()) < 5:
            return False, None, "Câu trả lời không đủ độ dài hợp lệ"

        # 5. Passed all grounding checks
        return True, reply, f"Hợp lệ ({grounding_note})"

    def process_and_reply_live(
        self,
        comment: ExtractedComment,
        active_product: Optional[Product],
        playout_engine: Optional[PlayoutEngine] = None,
        shop_policies: Optional[Dict[str, str]] = None,
        avatar_id: Optional[int | str] = None,
    ) -> Optional[str]:
        """Process an extracted comment, verify grounding, and if valid, enqueue reply clip into PlayoutEngine."""
        can_reply, reply_text, reason = self.evaluate_comment_for_reply(
            comment=comment,
            active_product=active_product,
            shop_policies=shop_policies,
        )

        if not can_reply or not reply_text:
            logger.debug("Không tự động trả lời @{}: {}", comment.username, reason)
            return None

        # Autonomous reply dispatch
        if self.auto_reply_enabled and playout_engine is not None:
            logger.info("Chấp thuận trả lời tự động cho @{}: '{}'", comment.username, reply_text)
            self.reply_clip_manager.enqueue_reply_to_engine(
                engine=playout_engine,
                reply_text=reply_text,
                customer_name=comment.username,
                avatar_id=avatar_id,
                product_id=active_product.id if active_product else None,
            )
            return reply_text

        return reply_text
