"""Comment Assistant Repository."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import desc, select
from sqlalchemy.orm import Session, selectinload

from app.database.models.comment import AISuggestion, CommentEvent


class CommentRepository:
    """Repository for comment events and AI reply suggestions."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def _commit(self) -> None:
        try:
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    # ------------------------------------------------------------ comments
    def create_comment_event(
        self,
        username: str,
        content: str,
        category: str = "other",
        session_id: Optional[int] = None,
        product_id: Optional[int] = None,
        source: str = "manual",
        is_spam: bool = False,
    ) -> CommentEvent:
        event = CommentEvent(
            username=username,
            content=content,
            category=category,
            session_id=session_id,
            product_id=product_id,
            source=source,
            received_at=datetime.now(timezone.utc),
            is_processed=False,
            is_spam=is_spam,
        )
        self.session.add(event)
        self._commit()
        self.session.refresh(event)
        return event

    def list_recent_comments(self, limit: int = 100) -> Sequence[CommentEvent]:
        return self.session.scalars(
            select(CommentEvent)
            .options(selectinload(CommentEvent.suggestions))
            .order_by(desc(CommentEvent.received_at), desc(CommentEvent.id))
            .limit(limit)
        ).all()

    def get_comment(self, comment_id: int) -> Optional[CommentEvent]:
        return self.session.get(CommentEvent, comment_id)

    def delete_all(self) -> int:
        events = self.session.scalars(select(CommentEvent)).all()
        count = len(events)
        for e in events:
            self.session.delete(e)
        self._commit()
        return count

    # --------------------------------------------------------- suggestions
    def create_suggestion(
        self,
        comment_id: int,
        suggested_reply: str,
        confidence: float = 0.9,
        is_auto_reply: bool = False,
        product_id: Optional[int] = None,
        needs_data: bool = False,
        grounding_note: Optional[str] = None,
    ) -> AISuggestion:
        suggestion = AISuggestion(
            comment_id=comment_id,
            product_id=product_id,
            suggested_reply=suggested_reply,
            confidence=confidence,
            status="pending",
            is_auto_reply=is_auto_reply,
            needs_data=needs_data,
            grounding_note=grounding_note,
        )
        self.session.add(suggestion)
        self._commit()
        self.session.refresh(suggestion)
        return suggestion

    def get_suggestion(self, suggestion_id: int) -> Optional[AISuggestion]:
        return self.session.get(AISuggestion, suggestion_id)

    def update_suggestion(
        self,
        suggestion_id: int,
        *,
        status: Optional[str] = None,
        suggested_reply: Optional[str] = None,
        approved_by: Optional[str] = None,
        sent_at: Optional[datetime] = None,
        send_error: Optional[str] = None,
    ) -> Optional[AISuggestion]:
        s = self.get_suggestion(suggestion_id)
        if not s:
            return None
        if status is not None:
            s.status = status
        if suggested_reply is not None:
            s.suggested_reply = suggested_reply
        if approved_by is not None:
            s.approved_by = approved_by
        if sent_at is not None:
            s.sent_at = sent_at
        if send_error is not None:
            s.send_error = send_error
        self._commit()
        self.session.refresh(s)
        return s

    def list_pending(self, limit: int = 100) -> Sequence[AISuggestion]:
        return self.session.scalars(
            select(AISuggestion)
            .where(AISuggestion.status == "pending")
            .order_by(desc(AISuggestion.created_at))
            .limit(limit)
        ).all()
