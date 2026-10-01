"""Application configuration using Pydantic Settings."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def get_app_data_dir() -> Path:
    """Get the application data directory on Windows."""
    app_data = os.environ.get("LOCALAPPDATA", "")
    if not app_data:
        app_data = str(Path.home() / "AppData" / "Local")
    return Path(app_data) / "LiveFlowAIStudio"


def get_default_data_dir() -> Path:
    """Get the default data directory, creating it if needed."""
    data_dir = get_app_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


class AppSettings(BaseSettings):
    """Main application settings, loaded from .env or environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="LIVEFLOW_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Application ---
    app_name: str = "LiveFlow AI Studio"
    app_version: str = "0.1.0"
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
    obs_password_key: str = "liveflow_obs_password"

    # --- AI / LLM ---
    llm_provider: str = "gemini"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"
    llm_model: str = "gemini-3.5-flash"
    llm_api_key_name: str = "liveflow_llm_api_key"
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

    # --- Media ---
    media_dir: Path | None = None
    max_import_size_mb: int = 2048
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"

    @property
    def effective_media_dir(self) -> Path:
        d = self.media_dir or (self.data_dir / "media")
        d.mkdir(parents=True, exist_ok=True)
        return d

    # --- TikTok ---
    tiktok_integration_mode: Literal["manual", "api", "demo"] = "manual"

    # --- Scheduler ---
    scheduler_default_gap_seconds: int = 5
    scheduler_max_retries: int = 3

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
