"""Comment Assistant Repository."""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from app.database.models.comment import CommentEvent, AISuggestion


class CommentRepository:
    """Repository for Comment events and AI response suggestions."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create_comment_event(
        self,
        username: str,
        content: str,
        category: str = "general",
        session_id: Optional[int] = None,
    ) -> CommentEvent:
        event = CommentEvent(
            username=username,
            content=content,
            category=category,
            session_id=session_id,
            received_at=datetime.now(),
            is_processed=False,
            is_spam=False,
        )
        self.session.add(event)
        self.session.commit()
        self.session.refresh(event)
        return event

    def create_suggestion(
        self,
        comment_id: int,
        suggested_reply: str,
        confidence: float = 0.9,
        is_auto_reply: bool = False,
    ) -> AISuggestion:
        suggestion = AISuggestion(
            comment_id=comment_id,
            suggested_reply=suggested_reply,
            confidence=confidence,
            status="pending",
            is_auto_reply=is_auto_reply,
        )
        self.session.add(suggestion)
        self.session.commit()
        self.session.refresh(suggestion)
        return suggestion

    def list_recent_comments(self, limit: int = 50) -> Sequence[CommentEvent]:
        return self.session.scalars(
            select(CommentEvent).order_by(desc(CommentEvent.received_at)).limit(limit)
        ).all()
