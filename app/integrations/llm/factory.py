"""Factory for creating LLM provider instances based on configuration."""

from __future__ import annotations

import os
from typing import Optional

from app.config.settings import get_settings
from app.integrations.llm.base import BaseLLMProvider
from app.integrations.llm.gemini_provider import GeminiLLMProvider
from app.integrations.llm.openai_provider import OpenAILLMProvider
from app.services.settings_service import SettingsService


def get_llm_provider(settings_service: Optional[SettingsService] = None) -> BaseLLMProvider:
    """Instantiate the active LLM provider based on DB settings or environment."""
    app_settings = get_settings()

    provider_name = ""
    if settings_service:
        provider_name = settings_service.get_setting("llm_provider", "").strip().lower()

    if not provider_name:
        provider_name = (app_settings.llm_provider or "gemini").strip().lower()

    if provider_name == "openai":
        api_key = ""
        base_url = ""
        model_name = "gpt-4o-mini"
        if settings_service:
            api_key = settings_service.get_setting("openai_api_key", "").strip()
            base_url = settings_service.get_setting("openai_base_url", "").strip()
            model_name = settings_service.get_setting("openai_model", "gpt-4o-mini").strip() or "gpt-4o-mini"

        if not api_key:
            api_key = os.environ.get("OPENAI_API_KEY", "")
        if not base_url:
            base_url = os.environ.get("OPENAI_BASE_URL", "")

        return OpenAILLMProvider(
            api_key=api_key or None,
            base_url=base_url or None,
            model_name=model_name,
        )

    # Default to Gemini
    api_key = ""
    model_name = "gemini-3.5-flash"
    if settings_service:
        api_key = settings_service.get_setting("gemini_api_key", "").strip()
        model_name = settings_service.get_setting("gemini_model", "gemini-3.5-flash").strip() or "gemini-3.5-flash"

    if not api_key:
        api_key = (
            os.environ.get("GEMINI_API_KEY")
            or os.environ.get("LIVEFLOW_GEMINI_API_KEY")
            or getattr(app_settings, "gemini_api_key", "")
        ).strip()

    if not model_name:
        model_name = getattr(app_settings, "gemini_model", "gemini-3.5-flash")

    return GeminiLLMProvider(
        api_key=api_key or None,
        model_name=model_name,
    )
