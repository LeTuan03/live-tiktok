"""Comment Service for processing incoming live viewer comments."""

from __future__ import annotations

import re
from typing import Optional, Sequence
from loguru import logger

from app.database.models.comment import CommentEvent, AISuggestion
from app.database.repositories.comment_repo import CommentRepository


class CommentService:
    """Service layer for comment processing and AI response generation."""

    def __init__(self, comment_repo: CommentRepository) -> None:
        self.comment_repo = comment_repo

    def process_comment(self, username: str, comment_text: str) -> tuple[CommentEvent, AISuggestion]:
        """Process incoming comment and generate AI response suggestion."""
        category = "general"
        if re.search(r"báo giá|bao nhieu|nhiêu tiền|giá", comment_text, re.IGNORECASE):
            category = "price"
            reply = "Dạ giá sản phẩm đang có ưu đãi khủng ngay trên giỏ hàng đó ạ!"
        elif re.search(r"ship|vận chuyển|giao hàng|bao lâu", comment_text, re.IGNORECASE):
            category = "shipping"
            reply = "Dạ bên em freeship toàn quốc, giao siêu tốc 2-3 ngày ạ!"
        elif re.search(r"chính hãng|auth|thật không", comment_text, re.IGNORECASE):
            category = "trust"
            reply = "Dạ hàng chuẩn chính hãng 100% đầy đủ hóa đơn bảo hành ạ!"
        else:
            reply = "Dạ cảm ơn bạn đã quan tâm! Bạn nhấp vào giỏ hàng xem chi tiết nha!"

        event = self.comment_repo.create_comment_event(
            username=username,
            content=comment_text,
            category=category,
        )
        suggestion = self.comment_repo.create_suggestion(
            comment_id=event.id,
            suggested_reply=reply,
            is_auto_reply=True,
        )
        return event, suggestion

    def get_recent_comments(self, limit: int = 50) -> Sequence[CommentEvent]:
        return self.comment_repo.list_recent_comments(limit)
