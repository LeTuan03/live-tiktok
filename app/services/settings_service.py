"""Settings Service — runtime configuration and secret storage.

Non-sensitive values live in the ``app_settings`` table. Sensitive values
(API keys, passwords, stream keys) are stored in the Windows Credential
Manager through :mod:`app.core.secrets`; the database only keeps a marker so
we know a value exists. When no credential store is available the value falls
back to the database with a logged warning, so the app keeps working on
unusual setups.
"""

from __future__ import annotations

from typing import Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import secrets
from app.database.models.settings import AppSetting

KEYRING_MARKER = "<keyring>"

# Keys that must always be treated as secrets regardless of the caller flag.
SECRET_KEYS = {
    "gemini_api_key",
    "openai_api_key",
    "obs_password",
    "stream_key_tiktok",
    "stream_key_shopee",
}

# Well-known setting keys (single source of truth for the UI and services).
KEY_LLM_PROVIDER = "llm_provider"
KEY_GEMINI_API_KEY = "gemini_api_key"
KEY_GEMINI_MODEL = "gemini_model"
KEY_OPENAI_API_KEY = "openai_api_key"
KEY_OPENAI_BASE_URL = "openai_base_url"
KEY_OPENAI_MODEL = "openai_model"
KEY_OBS_HOST = "obs_host"
KEY_OBS_PORT = "obs_port"
KEY_OBS_PASSWORD = "obs_password"
KEY_OBS_SCENE = "obs_scene_name"
KEY_OBS_VIDEO_SOURCE = "obs_video_source"
KEY_OBS_AUDIO_SOURCE = "obs_audio_source"
KEY_OBS_IMAGE_SOURCE = "obs_image_source"
KEY_OBS_TEXT_SOURCE = "obs_text_source"
KEY_OBS_SHOW_TEXT = "obs_show_text"
KEY_OBS_AUTO_SWITCH_SCENE = "obs_auto_switch_scene"
KEY_PLATFORM = "default_platform"
KEY_STREAM_SERVER_TIKTOK = "stream_server_tiktok"
KEY_STREAM_KEY_TIKTOK = "stream_key_tiktok"
KEY_STREAM_SERVER_SHOPEE = "stream_server_shopee"
KEY_STREAM_KEY_SHOPEE = "stream_key_shopee"
KEY_SHOP_NAME = "shop_name"
KEY_SHOP_SHIPPING_POLICY = "shop_shipping_policy"
KEY_SHOP_RETURN_POLICY = "shop_return_policy"
KEY_SHOP_WARRANTY_POLICY = "shop_warranty_policy"
KEY_COMMENT_HONORIFIC = "comment_honorific"
KEY_FFMPEG_DIR = "ffmpeg_dir"
KEY_CLOSE_TO_TRAY = "close_to_tray"
KEY_ONBOARDING_DONE = "onboarding_done"


class SettingsService:
    """Service layer for managing system settings and secret tokens."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------ core
    def _get_row(self, key: str) -> Optional[AppSetting]:
        return self.session.scalars(
            select(AppSetting).where(AppSetting.setting_key == key)
        ).first()

    def get_setting(self, key: str, default: str = "") -> str:
        """Return a setting value, resolving secrets from the credential store."""
        try:
            row = self._get_row(key)
        except Exception:
            self.session.rollback()
            raise

        if row is None or row.setting_value is None:
            # A secret may exist in the credential store without a DB marker
            # (e.g. after a database reset). Try the store before giving up.
            if key in SECRET_KEYS:
                value = secrets.get_secret(key)
                if value:
                    return value
            return default

        if row.setting_value == KEYRING_MARKER:
            value = secrets.get_secret(key)
            return value if value is not None else default

        return row.setting_value

    def set_setting(self, key: str, value: str, is_secret: bool = False) -> None:
        """Persist a setting. Secrets go to the credential store when available."""
        value = value if value is not None else ""
        store_value = value

        if is_secret or key in SECRET_KEYS:
            if secrets.set_secret(key, value):
                store_value = KEYRING_MARKER if value else ""
            else:
                if value:
                    logger.warning(
                        "Credential Manager không khả dụng — lưu '{}' vào database cục bộ.", key
                    )
                store_value = value

        try:
            row = self._get_row(key)
            if row:
                row.setting_value = store_value
            else:
                self.session.add(AppSetting(setting_key=key, setting_value=store_value))
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    def delete_setting(self, key: str) -> None:
        try:
            row = self._get_row(key)
            if row:
                self.session.delete(row)
                self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        if key in SECRET_KEYS:
            secrets.delete_secret(key)

    # --------------------------------------------------------------- helpers
    def get_int(self, key: str, default: int) -> int:
        raw = self.get_setting(key, "")
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        raw = self.get_setting(key, "").strip().lower()
        if raw in ("1", "true", "yes", "on"):
            return True
        if raw in ("0", "false", "no", "off"):
            return False
        return default

    def set_bool(self, key: str, value: bool) -> None:
        self.set_setting(key, "1" if value else "0")

    def has_secret(self, key: str) -> bool:
        return bool(self.get_setting(key, ""))

    # ------------------------------------------------------------ composite
    def is_llm_configured(self) -> bool:
        """True when the active LLM provider has the credentials it needs."""
        from app.config.settings import get_settings

        app_settings = get_settings()
        provider = (self.get_setting(KEY_LLM_PROVIDER, "") or app_settings.llm_provider).lower()
        if provider == "openai":
            key = self.get_setting(KEY_OPENAI_API_KEY, "") or app_settings.openai_api_key
            base = self.get_setting(KEY_OPENAI_BASE_URL, "") or app_settings.openai_base_url
            return bool(key or base)
        key = self.get_setting(KEY_GEMINI_API_KEY, "") or app_settings.gemini_api_key
        return bool(key)

    def get_obs_connection(self) -> tuple[str, int, str]:
        """Return (host, port, password) for OBS WebSocket."""
        from app.config.settings import get_settings

        app_settings = get_settings()
        host = self.get_setting(KEY_OBS_HOST, "") or app_settings.obs_host
        port = self.get_int(KEY_OBS_PORT, app_settings.obs_port)
        password = self.get_setting(KEY_OBS_PASSWORD, "")
        return host, port, password

    def get_obs_sources(self) -> tuple[str, str, str]:
        """Return (scene_name, video_source, audio_source) controlled by LiveFlow."""
        from app.config.settings import get_settings

        app_settings = get_settings()
        scene = self.get_setting(KEY_OBS_SCENE, "") or app_settings.obs_scene_name
        video = self.get_setting(KEY_OBS_VIDEO_SOURCE, "") or app_settings.obs_video_source
        audio = self.get_setting(KEY_OBS_AUDIO_SOURCE, "") or app_settings.obs_audio_source
        return scene, video, audio

    def get_obs_extra_sources(self) -> tuple[str, str]:
        """Return (image_source, text_source) names controlled by LiveFlow."""
        from app.config.settings import get_settings

        app_settings = get_settings()
        image = self.get_setting(KEY_OBS_IMAGE_SOURCE, "") or app_settings.obs_image_source
        text = self.get_setting(KEY_OBS_TEXT_SOURCE, "") or app_settings.obs_text_source
        return image, text

    def get_platform(self) -> str:
        from app.config.settings import get_settings

        return (self.get_setting(KEY_PLATFORM, "") or get_settings().default_platform).lower()

    def get_shop_policies(self) -> dict[str, str]:
        return {
            "shop_name": self.get_setting(KEY_SHOP_NAME, ""),
            "shipping": self.get_setting(KEY_SHOP_SHIPPING_POLICY, ""),
            "return": self.get_setting(KEY_SHOP_RETURN_POLICY, ""),
            "warranty": self.get_setting(KEY_SHOP_WARRANTY_POLICY, ""),
        }
