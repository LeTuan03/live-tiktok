"""AIAvatar repository for managing AI digital presenter profiles."""

from __future__ import annotations

from typing import Optional, Sequence
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from app.database.models.avatar import AIAvatar


class AvatarRepository:
    """Repository for managing AIAvatar entities."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def _commit(self) -> None:
        try:
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    def get_by_id(self, avatar_id: int) -> Optional[AIAvatar]:
        return self.session.get(AIAvatar, avatar_id)

    def get_all(self, active_only: bool = True) -> Sequence[AIAvatar]:
        stmt = select(AIAvatar)
        if active_only:
            stmt = stmt.where(AIAvatar.is_active.is_(True))
        stmt = stmt.order_by(desc(AIAvatar.is_default), AIAvatar.name)
        return self.session.execute(stmt).scalars().all()

    def get_default(self) -> Optional[AIAvatar]:
        stmt = (
            select(AIAvatar)
            .where(AIAvatar.is_default.is_(True), AIAvatar.is_active.is_(True))
            .limit(1)
        )
        return self.session.execute(stmt).scalars().first()

    def _unset_default(self) -> None:
        stmt = select(AIAvatar).where(AIAvatar.is_default.is_(True))
        defaults = self.session.execute(stmt).scalars().all()
        for d in defaults:
            d.is_default = False

    def create(
        self,
        name: str,
        gender: str = "female",
        style: str = "Thanh lịch",
        avatar_type: str = "video_loop",
        media_path: Optional[str] = None,
        preview_image_path: Optional[str] = None,
        transparent_bg: bool = True,
        chroma_key_color: str = "#00FF00",
        voice_id: str = "vi-VN-HoaiMyNeural",
        description: Optional[str] = None,
        is_default: bool = False,
        is_active: bool = True,
        obs_position: str = "bottom_right",
        obs_scale: float = 0.35,
    ) -> AIAvatar:
        if is_default:
            self._unset_default()

        avatar = AIAvatar(
            name=name,
            gender=gender,
            style=style,
            avatar_type=avatar_type,
            media_path=media_path,
            preview_image_path=preview_image_path,
            transparent_bg=transparent_bg,
            chroma_key_color=chroma_key_color,
            voice_id=voice_id,
            description=description,
            is_default=is_default,
            is_active=is_active,
            obs_position=obs_position,
            obs_scale=obs_scale,
        )
        self.session.add(avatar)
        self._commit()
        self.session.refresh(avatar)
        return avatar

    def update(
        self,
        avatar_id: int,
        **kwargs,
    ) -> Optional[AIAvatar]:
        avatar = self.get_by_id(avatar_id)
        if not avatar:
            return None

        if kwargs.get("is_default"):
            self._unset_default()

        for key, value in kwargs.items():
            if hasattr(avatar, key):
                setattr(avatar, key, value)

        self._commit()
        self.session.refresh(avatar)
        return avatar

    def delete(self, avatar_id: int) -> bool:
        avatar = self.get_by_id(avatar_id)
        if not avatar:
            return False
        self.session.delete(avatar)
        self._commit()
        return True

    def count(self) -> int:
        from sqlalchemy import func
        stmt = select(func.count(AIAvatar.id))
        return self.session.execute(stmt).scalar() or 0
