"""Unit tests for LLM providers and factory."""

import unittest
from unittest.mock import patch, MagicMock

from app.database.session import init_database, get_session
from app.integrations.llm.base import BaseLLMProvider
from app.integrations.llm.factory import get_llm_provider
from app.integrations.llm.gemini_provider import GeminiLLMProvider
from app.integrations.llm.openai_provider import OpenAILLMProvider
from app.services.settings_service import SettingsService


class TestLLMProviders(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        init_database("sqlite:///:memory:")

    def setUp(self):
        self.session = get_session()
        self.settings_service = SettingsService(self.session)

    def tearDown(self):
        self.session.close()

    def test_gemini_key_validation_empty(self):
        provider = GeminiLLMProvider(api_key="")
        with self.assertRaises(ValueError) as ctx:
            provider._validate_api_key()
        self.assertIn("Chưa cấu hình Google Gemini API Key", str(ctx.exception))

    def test_gemini_key_validation_truncated(self):
        # When user copies masked key ...avVQ
        provider = GeminiLLMProvider(api_key="...avVQ")
        with self.assertRaises(ValueError) as ctx:
            provider._validate_api_key()
        self.assertIn("bị rút gọn", str(ctx.exception))

    def test_gemini_key_validation_valid(self):
        provider = GeminiLLMProvider(api_key="AIzaSyDummyKeyForTestingPurposes123456789")
        try:
            provider._validate_api_key()
        except ValueError:
            self.fail("_validate_api_key raised ValueError unexpectedly!")

    def test_factory_returns_gemini_by_default(self):
        self.settings_service.set_setting("llm_provider", "gemini")
        self.settings_service.set_setting("gemini_api_key", "AIzaSyDummyKeyForTestingPurposes123456789")
        self.settings_service.set_setting("gemini_model", "gemini-1.5-flash")

        provider = get_llm_provider(self.settings_service)
        self.assertIsInstance(provider, GeminiLLMProvider)
        self.assertEqual(provider.model_name, "gemini-1.5-flash")

    def test_factory_returns_openai_when_selected(self):
        self.settings_service.set_setting("llm_provider", "openai")
        self.settings_service.set_setting("openai_api_key", "sk-dummy-key")
        self.settings_service.set_setting("openai_model", "gpt-4o")

        provider = get_llm_provider(self.settings_service)
        self.assertIsInstance(provider, OpenAILLMProvider)
        self.assertEqual(provider.model_name, "gpt-4o")


if __name__ == "__main__":
    unittest.main()
