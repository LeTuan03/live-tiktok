"""OpenAI & OpenAI-compatible LLM provider implementation (Ollama, LM Studio, vLLM...)."""

from __future__ import annotations

from typing import List, Optional

from loguru import logger

from app.integrations.llm.base import BaseLLMProvider


class OpenAILLMProvider(BaseLLMProvider):
    """LLM Provider for OpenAI API and compatible servers."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model_name: str = "gpt-4o-mini",
        timeout: int = 60,
    ) -> None:
        self.api_key = (api_key or "").strip() or None
        self.base_url = (base_url or "").strip() or None
        self.model_name = (model_name or "gpt-4o-mini").strip()
        self.timeout = timeout

    def _client(self):
        try:
            import openai
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("Thư viện 'openai' chưa được cài đặt. Chạy: pip install openai") from e

        if not self.api_key and not self.base_url:
            raise ValueError(
                "Chưa cấu hình API Key hoặc Base URL cho OpenAI provider. Vào 'Cài đặt' để nhập."
            )
        return openai.AsyncOpenAI(
            api_key=self.api_key or "not-needed",
            base_url=self.base_url,
            timeout=self.timeout,
            max_retries=2,
        )

    async def list_models(self) -> List[str]:
        client = self._client()
        try:
            res = await client.models.list()
            return sorted(m.id for m in getattr(res, "data", []) or [])
        except Exception as e:
            raise RuntimeError(f"Không thể tải danh sách model: {e}") from e

    async def generate_text(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> str:
        client = self._client()

        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        logger.info("Gửi request tới LLM model: {} ({})", self.model_name, self.base_url or "api.openai.com")

        try:
            response = await client.chat.completions.create(
                model=self.model_name,
                messages=messages,  # type: ignore[arg-type]
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as e:
            # Normalise SDK errors into a readable Vietnamese message.
            name = e.__class__.__name__
            detail = str(e)
            if "AuthenticationError" in name or "401" in detail:
                raise RuntimeError("API Key OpenAI không hợp lệ hoặc đã hết hạn.") from e
            if "RateLimit" in name or "429" in detail:
                raise RuntimeError("OpenAI đang giới hạn tốc độ (429). Vui lòng thử lại sau.") from e
            if "Connection" in name or "connect" in detail.lower():
                raise RuntimeError(
                    f"Không thể kết nối tới máy chủ LLM ({self.base_url or 'api.openai.com'}). "
                    "Kiểm tra mạng hoặc Base URL."
                ) from e
            raise RuntimeError(f"Lỗi từ LLM provider: {detail}") from e

        content = response.choices[0].message.content or ""
        return content.strip()
