"""OpenAI & OpenAI-compatible LLM provider implementation."""

from __future__ import annotations

from typing import Optional
import openai
from loguru import logger
from app.integrations.llm.base import BaseLLMProvider


class OpenAILLMProvider(BaseLLMProvider):
    """LLM Provider for OpenAI API and compatible servers (Ollama, LMStudio, etc.)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model_name: str = "gpt-4o-mini",
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.model_name = model_name

    async def generate_text(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> str:
        if not self.api_key and not self.base_url:
            raise ValueError("Chưa cấu hình API Key hoặc Base URL cho AI LLM Provider.")

        client = openai.AsyncOpenAI(
            api_key=self.api_key or "dummy-key",
            base_url=self.base_url or None,
        )

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        logger.info(f"Đang gửi request tới LLM model: {self.model_name}")

        response = await client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        content = response.choices[0].message.content or ""
        return content.strip()
