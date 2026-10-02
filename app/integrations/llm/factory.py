"""Factory for creating LLM provider instances based on configuration."""

from __future__ import annotations

import os
from typing import Optional

from app.config.settings import get_settings
from app.core.exceptions import AIProviderNotConfiguredError
from app.integrations.llm.base import BaseLLMProvider
from app.integrations.llm.gemini_provider import GeminiLLMProvider
from app.integrations.llm.openai_provider import OpenAILLMProvider
from app.services.settings_service import (
    KEY_GEMINI_API_KEY,
    KEY_GEMINI_MODEL,
    KEY_LLM_PROVIDER,
    KEY_OPENAI_API_KEY,
    KEY_OPENAI_BASE_URL,
    KEY_OPENAI_MODEL,
    SettingsService,
)


def get_llm_provider(settings_service: Optional[SettingsService] = None) -> BaseLLMProvider:
    """Instantiate the active LLM provider.

    Resolution order: database settings (UI) → environment / .env → defaults.
    Raises :class:`AIProviderNotConfiguredError` when no credentials exist so
    callers can show a clear message instead of sending data to nowhere.
    """
    app_settings = get_settings()

    provider_name = ""
    if settings_service:
        provider_name = settings_service.get_setting(KEY_LLM_PROVIDER, "").strip().lower()
    if not provider_name:
        provider_name = (app_settings.llm_provider or "gemini").strip().lower()

    if provider_name == "openai":
        api_key = base_url = model_name = ""
        if settings_service:
            api_key = settings_service.get_setting(KEY_OPENAI_API_KEY, "").strip()
            base_url = settings_service.get_setting(KEY_OPENAI_BASE_URL, "").strip()
            model_name = settings_service.get_setting(KEY_OPENAI_MODEL, "").strip()

        api_key = api_key or os.environ.get("OPENAI_API_KEY", "") or app_settings.openai_api_key
        base_url = base_url or os.environ.get("OPENAI_BASE_URL", "") or app_settings.openai_base_url
        model_name = model_name or app_settings.openai_model or "gpt-4o-mini"

        if not api_key and not base_url:
            raise AIProviderNotConfiguredError(
                "Chưa cấu hình OpenAI API Key (hoặc Base URL cho server tương thích). "
                "Vào 'Cài đặt' → 'AI Provider' để nhập."
            )
        return OpenAILLMProvider(
            api_key=api_key or None,
            base_url=base_url or None,
            model_name=model_name,
            timeout=app_settings.llm_timeout,
        )

    # Default: Gemini
    api_key = model_name = ""
    if settings_service:
        api_key = settings_service.get_setting(KEY_GEMINI_API_KEY, "").strip()
        model_name = settings_service.get_setting(KEY_GEMINI_MODEL, "").strip()

    api_key = (
        api_key
        or os.environ.get("GEMINI_API_KEY", "")
        or os.environ.get("LIVEFLOW_GEMINI_API_KEY", "")
        or app_settings.gemini_api_key
    ).strip()
    model_name = model_name or app_settings.gemini_model or GeminiLLMProvider.DEFAULT_MODEL

    if not api_key:
        raise AIProviderNotConfiguredError(
            "Chưa cấu hình Google Gemini API Key. Vào 'Cài đặt' → 'AI Provider' để nhập "
            "(lấy key miễn phí tại https://aistudio.google.com/app/apikey)."
        )
    return GeminiLLMProvider(api_key=api_key, model_name=model_name, timeout=app_settings.llm_timeout)


def is_llm_configured(settings_service: Optional[SettingsService] = None) -> bool:
    try:
        get_llm_provider(settings_service)
        return True
    except AIProviderNotConfiguredError:
        return False
    except Exception:
        return False
