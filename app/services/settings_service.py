"""Settings Service for application configuration and integration keys."""

from __future__ import annotations

from typing import Optional
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models.settings import AppSetting
from app.database.session import get_session


class SettingsService:
    """Service layer for managing system settings and secret tokens."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_setting(self, key: str, default: str = "") -> str:
        setting = self.session.scalars(
            select(AppSetting).where(AppSetting.setting_key == key)
        ).first()
        return setting.setting_value if setting and setting.setting_value is not None else default

    def set_setting(self, key: str, value: str, is_secret: bool = False) -> None:
        setting = self.session.scalars(
            select(AppSetting).where(AppSetting.setting_key == key)
        ).first()
        if setting:
            setting.setting_value = value
        else:
            setting = AppSetting(setting_key=key, setting_value=value)
            self.session.add(setting)
        self.session.commit()

