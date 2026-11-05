"""Application configuration using Pydantic Settings.

Configuration precedence (highest first):
    1. Environment variables prefixed with ``LIVEFLOW_``
    2. ``.env`` file in the project root (next to ``pyproject.toml``)
    3. ``.env`` file in the user data directory (%LOCALAPPDATA%/LiveFlowAIStudio)
    4. Defaults defined below

Runtime-editable settings (API keys, OBS host, ...) are stored in the SQLite
database / Windows Credential Manager via ``SettingsService`` and take
precedence over the values here when present.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_NAME = "LiveFlow AI Studio"
APP_VERSION = "1.0.0"


def get_project_root() -> Path:
    """Return the project root (source checkout) or the frozen executable directory."""
    if getattr(sys, "frozen", False):  # PyInstaller bundle
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def get_app_data_dir() -> Path:
    """Get the per-user application data directory on Windows (or ~/.local/share elsewhere)."""
    app_data = os.environ.get("LOCALAPPDATA", "")
    if not app_data:
        if sys.platform == "win32":
            app_data = str(Path.home() / "AppData" / "Local")
        else:
            app_data = str(Path.home() / ".local" / "share")
    return Path(app_data) / "LiveFlowAIStudio"


def get_default_data_dir() -> Path:
    """Get the default data directory, creating it if needed."""
    data_dir = get_app_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _env_files() -> tuple[str, ...]:
    """Candidate .env files. Later entries override earlier ones in pydantic-settings."""
    candidates = [
        get_app_data_dir() / ".env",
        get_project_root() / ".env",
    ]
    return tuple(str(p) for p in candidates)


class AppSettings(BaseSettings):
    """Main application settings, loaded from .env or environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="LIVEFLOW_",
        env_file=_env_files(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Application ---
    app_name: str = APP_NAME
    app_version: str = APP_VERSION
    debug: bool = False
    language: str = "vi"
    data_dir: Path = Field(default_factory=get_default_data_dir)

    # --- Database ---
    db_name: str = "liveflow.db"

    @property
    def db_url(self) -> str:
        db_path = self.data_dir / self.db_name
        return f"sqlite:///{db_path}"

    # --- Logging ---
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_dir: Path | None = None

    @property
    def effective_log_dir(self) -> Path:
        d = self.log_dir or (self.data_dir / "logs")
        d.mkdir(parents=True, exist_ok=True)
        return d

    # --- OBS ---
    obs_host: str = "localhost"
    obs_port: int = 4455
    obs_password_key: str = "obs_password"
    # Names of the OBS scene/inputs that LiveFlow is allowed to control.
    # They are created by the app (with user confirmation) and never touch other scenes.
    obs_scene_name: str = "LiveFlow AI"
    obs_video_source: str = "LiveFlow Video"
    obs_audio_source: str = "LiveFlow Audio"
    obs_image_source: str = "LiveFlow Image"
    obs_text_source: str = "LiveFlow Text"
    obs_avatar_source: str = "LiveFlow Avatar"
    obs_reconnect_max_attempts: int = 5

    # --- AI / LLM ---
    llm_provider: str = "gemini"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"
    llm_model: str = "gemini-3.5-flash"
    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_model: str = "gpt-4o-mini"
    llm_max_concurrent: int = 3
    llm_timeout: int = 60

    # --- TTS ---
    tts_provider: str = "edge_tts"
    tts_default_voice: str = "vi-VN-HoaiMyNeural"
    tts_cache_dir: Path | None = None

    @property
    def effective_tts_cache_dir(self) -> Path:
        d = self.tts_cache_dir or (self.data_dir / "tts_cache")
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def effective_audio_dir(self) -> Path:
        """Directory holding generated TTS audio files for scripts."""
        d = self.data_dir / "audio"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # --- Media ---
    media_dir: Path | None = None
    max_import_size_mb: int = 4096
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"

    @property
    def effective_media_dir(self) -> Path:
        d = self.media_dir or (self.data_dir / "media")
        d.mkdir(parents=True, exist_ok=True)
        return d

    # --- Avatar / Digital Presenter ---
    avatar_dir: Path | None = None
    did_api_key: str = ""
    heygen_api_key: str = ""

    # --- Google AI video generation (Gemini Omni / Veo) ---
    # Veo 3.1 *preview* models were retired on Google AI Studio; default is Gemini Omni Flash,
    # which works with standard Gemini API keys via the Interactions API.
    veo_model: str = "gemini-omni-1.1-flash"
    veo_fallback_model: str = ""
    veo_aspect_ratio: str = "9:16"
    veo_duration_seconds: int = 5
    veo_resolution: str = "720p"
    # When cloud generation fails: False = show the error; True = silently fallback to local 2D animator
    veo_allow_local_fallback: bool = False
    # True = keep speech generated directly by the video model (lip-synced); False = replace with Edge-TTS
    veo_use_native_audio: bool = True

    # --- Google Flow (flow.google.com) ---
    # "flow" = dùng tín dụng Flow của gói Google AI qua trình duyệt nhúng trong app (KHÔNG cần API key,
    #          không cần bật Billing). "api" = gọi thẳng Gemini/Veo API (phải bật Billing).
    video_source: Literal["flow", "api"] = "flow"
    flow_model: str = "veo-3.1-fast"  # veo-3.1-fast | veo-3.1-quality | veo-3.1-lite
    flow_resolution: str = "720p"
    flow_duration_seconds: int = 8
    # Số tín dụng Flow đang có. LiveFlow tự trừ dần; bấm "Đồng bộ" trong Flow Studio để chốt lại.
    flow_credits_total: int = 1050
    # > 0 = tự nhập giá tín dụng mỗi lần tạo, ghi đè bảng giá dựng sẵn (khi Google đổi giá).
    flow_credit_cost: int = 0
    flow_image_credit_cost: int = 0
    # Trình duyệt nhúng lưu video tải về vào đây; để trống = <data_dir>/flow_downloads
    flow_downloads_dir: Path | None = None
    # True = tự dán prompt + tự bấm Tạo + tự tải + tự nạp. False = chỉ dán prompt, người dùng tự bấm.
    flow_auto_generate: bool = True
    flow_auto_import: bool = True
    # Thời gian chờ tối đa cho một clip Flow render xong (giây)
    flow_wait_timeout_seconds: int = 420

    @property
    def effective_flow_dir(self) -> Path:
        """Thư mục chứa các gói việc Flow (prompt + manifest + ảnh tham chiếu)."""
        d = self.data_dir / "flow_packs"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def effective_flow_downloads_dir(self) -> Path:
        """Thư mục trình duyệt nhúng ghi file tải về (trước khi chuẩn hoá)."""
        d = Path(self.flow_downloads_dir) if self.flow_downloads_dir else (self.data_dir / "flow_downloads")
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def effective_avatar_dir(self) -> Path:
        d = self.avatar_dir or (self.data_dir / "avatars")
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def effective_veo_dir(self) -> Path:
        d = self.data_dir / "veo_scenes"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # --- Platform (TikTok / Shopee) ---
    tiktok_integration_mode: Literal["manual", "api", "demo"] = "manual"
    default_platform: Literal["tiktok", "shopee"] = "tiktok"

    # --- Scheduler ---
    scheduler_default_gap_seconds: int = 5
    scheduler_default_item_seconds: int = 90
    scheduler_max_consecutive_errors: int = 3

    @field_validator("data_dir", mode="before")
    @classmethod
    def ensure_data_dir(cls, v: str | Path) -> Path:
        p = Path(v)
        p.mkdir(parents=True, exist_ok=True)
        return p


# Singleton settings instance
_settings: AppSettings | None = None


def get_settings() -> AppSettings:
    """Get or create the singleton settings instance."""
    global _settings
    if _settings is None:
        _settings = AppSettings()
    return _settings


def reset_settings() -> None:
    """Reset settings (for testing)."""
    global _settings
    _settings = None
