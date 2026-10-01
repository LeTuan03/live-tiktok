"""Google Gemini LLM provider implementation."""

from __future__ import annotations

from typing import Optional
import aiohttp
from loguru import logger

from app.integrations.llm.base import BaseLLMProvider


class GeminiLLMProvider(BaseLLMProvider):
    """LLM Provider for Google Gemini API with automatic model fallback."""

    DEFAULT_MODEL = "gemini-3.5-flash"
    API_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    FALLBACK_MODELS = [
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash-lite",
        "gemini-flash-latest",
        "gemini-3.8-flash",
    ]

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = "gemini-3.5-flash",
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.model_name = model_name or self.DEFAULT_MODEL

    def _validate_api_key(self) -> None:
        if not self.api_key:
            raise ValueError(
                "Chưa cấu hình Google Gemini API Key. "
                "Vui lòng vào 'Cài đặt' hoặc thêm LIVEFLOW_GEMINI_API_KEY vào tệp .env."
            )

        if self.api_key.startswith("...") or "..." in self.api_key or len(self.api_key) < 20:
            raise ValueError(
                "Gemini API Key không hợp lệ hoặc đang bị rút gọn (dạng '...xxxx'). "
                "Vui lòng truy cập Google AI Studio (https://aistudio.google.com/app/apikey), "
                "nhấp biểu tượng Copy để lấy đầy đủ API Key bắt đầu bằng 'AIzaSy...'."
            )

    async def generate_text(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2500,
    ) -> str:
        self._validate_api_key()

        # Build list of models to try (selected model first, then fallbacks)
        models_to_try = [self.model_name]
        for fb in self.FALLBACK_MODELS:
            if fb not in models_to_try:
                models_to_try.append(fb)

        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }

        if system_prompt:
            payload["systemInstruction"] = {
                "parts": [{"text": system_prompt}]
            }

        timeout = aiohttp.ClientTimeout(total=45)
        last_error = ""

        async with aiohttp.ClientSession(timeout=timeout) as session:
            for current_model in models_to_try:
                url = f"{self.API_URL_TEMPLATE.format(model=current_model)}?key={self.api_key}"
                logger.info(f"Đang gửi request tới Google Gemini model: {current_model}")

                try:
                    async with session.post(url, headers=headers, json=payload) as response:
                        data = await response.json()

                        if response.status == 200:
                            candidates = data.get("candidates", [])
                            if not candidates:
                                raise RuntimeError("Gemini không trả về kết quả nào (có thể do bộ lọc an toàn).")

                            content_parts = candidates[0].get("content", {}).get("parts", [])
                            if not content_parts:
                                return ""

                            generated_text = "".join(part.get("text", "") for part in content_parts)
                            return generated_text.strip()

                        # Non-200 status code
                        err_info = data.get("error", {})
                        err_msg = err_info.get("message", f"HTTP {response.status}")
                        last_error = f"Gemini API ({current_model}) [{response.status}]: {err_msg}"
                        logger.warning(last_error)

                        # If 404 (model deprecated) or 503 (high demand spike), try next model
                        if response.status in (404, 503, 429):
                            logger.info(f"Model {current_model} gặp lỗi {response.status}, thử model tiếp theo...")
                            continue
                        else:
                            raise RuntimeError(f"Lỗi từ Gemini API: {err_msg}")

                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    last_error = f"Lỗi kết nối tới Gemini ({current_model}): {e}"
                    logger.warning(last_error)
                    continue

        raise RuntimeError(f"Không thể sinh văn bản từ Google Gemini sau khi thử các model: {last_error}")
