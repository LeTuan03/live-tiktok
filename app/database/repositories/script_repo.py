"""Script Repository for database operations."""

from __future__ import annotations

from typing import Optional, Sequence
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from app.database.models.script import Script, ScriptVersion


class ScriptRepository:
    """Repository for managing Script and ScriptVersion entities."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        product_id: int,
        title: str,
        content: str,
        style: Optional[str] = None,
        target_audience: Optional[str] = None,
        language: str = "vi",
        speaking_speed: Optional[str] = None,
        honorific: Optional[str] = None,
        cta_text: Optional[str] = None,
        duration_seconds: Optional[float] = None,
    ) -> Script:

        # Archive old current scripts for this product if any
        existing_scripts = self.session.scalars(
            select(Script).where(Script.product_id == product_id, Script.is_current == True)
        ).all()
        for s in existing_scripts:
            s.is_current = False

        script = Script(
            product_id=product_id,
            title=title,
            content=content,
            style=style,
            target_audience=target_audience,
            language=language,
            speaking_speed=speaking_speed,
            honorific=honorific,
            cta_text=cta_text,
            duration_seconds=duration_seconds,
            version=1,
            is_current=True,
        )
        self.session.add(script)
        self.session.flush()

        # Add initial version
        version = ScriptVersion(
            script_id=script.id,
            version=1,
            content=content,
            change_note="Phiên bản khởi tạo",
        )
        self.session.add(version)
        self.session.commit()
        self.session.refresh(script)
        return script

    def update_content(
        self,
        script_id: int,
        new_content: str,
        change_note: Optional[str] = None,
        title: Optional[str] = None,
    ) -> Optional[Script]:
        script = self.get_by_id(script_id)
        if not script:
            return None

        script.version += 1
        script.content = new_content
        if title:
            script.title = title

        version = ScriptVersion(
            script_id=script.id,
            version=script.version,
            content=new_content,
            change_note=change_note or f"Cập nhật phiên bản {script.version}",
        )
        self.session.add(version)
        self.session.commit()
        self.session.refresh(script)
        return script

    def get_by_id(self, script_id: int) -> Optional[Script]:
        return self.session.get(Script, script_id)

    def get_by_product(self, product_id: int) -> Sequence[Script]:
        return self.session.scalars(
            select(Script)
            .where(Script.product_id == product_id)
            .order_by(desc(Script.updated_at))
        ).all()

    def get_current_for_product(self, product_id: int) -> Optional[Script]:
        return self.session.scalars(
            select(Script).where(Script.product_id == product_id, Script.is_current == True)
        ).first()

    def get_versions(self, script_id: int) -> Sequence[ScriptVersion]:
        return self.session.scalars(
            select(ScriptVersion)
            .where(ScriptVersion.script_id == script_id)
            .order_by(desc(ScriptVersion.version))
        ).all()

    def delete(self, script_id: int) -> bool:
        script = self.get_by_id(script_id)
        if not script:
            return False
        self.session.delete(script)
        self.session.commit()
        return True
