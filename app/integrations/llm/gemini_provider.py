"""Google Gemini LLM provider implementation (REST, aiohttp)."""

from __future__ import annotations

import asyncio
from typing import Any, List, Optional

import aiohttp
from loguru import logger

from app.integrations.llm.base import BaseLLMProvider

API_BASE = "https://generativelanguage.googleapis.com/v1beta"


class GeminiLLMProvider(BaseLLMProvider):
    """LLM Provider for Google Gemini API with automatic model fallback.

    The API key is sent in the ``x-goog-api-key`` header (never in the URL) so
    it cannot leak through exception messages or logs.
    """

    DEFAULT_MODEL = "gemini-3.5-flash"
    FALLBACK_MODELS = [
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
        "gemini-flash-latest",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash",
    ]

    def __init__(self, api_key: Optional[str] = None, model_name: str = DEFAULT_MODEL, timeout: int = 60) -> None:
        self.api_key = (api_key or "").strip()
        self.model_name = (model_name or self.DEFAULT_MODEL).strip()
        self.timeout = timeout

    # ------------------------------------------------------------- helpers
    def _validate_api_key(self) -> None:
        if not self.api_key:
            raise ValueError(
                "Chưa cấu hình Google Gemini API Key. "
                "Vui lòng vào 'Cài đặt' hoặc thêm LIVEFLOW_GEMINI_API_KEY vào tệp .env."
            )
        if "..." in self.api_key or len(self.api_key) < 20 or " " in self.api_key:
            raise ValueError(
                "Gemini API Key không hợp lệ hoặc đang bị rút gọn (dạng '...xxxx'). "
                "Vui lòng truy cập Google AI Studio (https://aistudio.google.com/app/apikey), "
                "nhấp biểu tượng Copy để sao chép đầy đủ API Key."
            )

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "x-goog-api-key": self.api_key}

    @staticmethod
    def _normalize(model: str) -> str:
        return model.replace("models/", "").strip()

    async def list_models(self) -> List[str]:
        """Return model ids that support generateContent (best first)."""
        self._validate_api_key()
        url = f"{API_BASE}/models?pageSize=200"
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=self._headers()) as resp:
                data = await resp.json(content_type=None)
                if resp.status != 200:
                    err = (data or {}).get("error", {}).get("message", f"HTTP {resp.status}")
                    raise RuntimeError(f"Không thể tải danh sách model Gemini: {err}")
        models: List[str] = []
        for m in data.get("models", []):
            methods = m.get("supportedGenerationMethods", []) or []
            name = self._normalize(m.get("name", ""))
            if "generateContent" in methods and name:
                models.append(name)
        # Prefer flash models (fast/cheap) then everything else, newest first.
        flash = sorted([m for m in models if "flash" in m], reverse=True)
        others = sorted([m for m in models if "flash" not in m], reverse=True)
        return flash + others

    # ------------------------------------------------------------ generate
    async def generate_text(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2500,
    ) -> str:
        self._validate_api_key()

        models_to_try = [self._normalize(self.model_name)]
        for fb in self.FALLBACK_MODELS:
            if fb not in models_to_try:
                models_to_try.append(fb)

        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        }
        if system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}

        timeout = aiohttp.ClientTimeout(total=self.timeout)
        last_error = ""
        discovered = False

        async with aiohttp.ClientSession(timeout=timeout) as session:
            idx = 0
            while idx < len(models_to_try):
                current_model = models_to_try[idx]
                idx += 1
                url = f"{API_BASE}/models/{current_model}:generateContent"
                logger.info("Gửi request tới Google Gemini model: {}", current_model)

                try:
                    async with session.post(url, headers=self._headers(), json=payload) as response:
                        data = await response.json(content_type=None)

                        if response.status == 200:
                            candidates = (data or {}).get("candidates", [])
                            if not candidates:
                                block = (data or {}).get("promptFeedback", {}).get("blockReason", "")
                                raise RuntimeError(
                                    "Gemini không trả về kết quả nào"
                                    + (f" (bị chặn: {block})." if block else " (có thể do bộ lọc an toàn).")
                                )
                            parts = candidates[0].get("content", {}).get("parts", [])
                            text = "".join(p.get("text", "") for p in parts).strip()
                            if text:
                                if current_model != self._normalize(self.model_name):
                                    logger.info("Đã dùng model dự phòng: {}", current_model)
                                return text
                            last_error = f"Model {current_model} trả về nội dung rỗng."
                            continue

                        err_info = (data or {}).get("error", {})
                        err_msg = err_info.get("message", f"HTTP {response.status}")
                        last_error = f"Gemini API ({current_model}) [{response.status}]: {err_msg}"
                        logger.warning(last_error)

                        if response.status in (400, 401, 403) and (
                            "api key" in err_msg.lower() or "permission" in err_msg.lower()
                        ):
                            raise RuntimeError(
                                "API Key Gemini không hợp lệ hoặc không có quyền. "
                                f"Chi tiết: {err_msg}"
                            )

                        if response.status in (404, 429, 503, 500):
                            # Model missing/overloaded → try the next one. On the first 404
                            # also ask the API which models actually exist for this key.
                            if response.status == 404 and not discovered:
                                discovered = True
                                try:
                                    available = await self.list_models()
                                    for m in available:
                                        if m not in models_to_try:
                                            models_to_try.append(m)
                                    logger.info("Model khả dụng từ API: {}", available[:8])
                                except Exception as le:  # noqa: BLE001
                                    logger.debug("Không thể liệt kê model: {}", le)
                            continue

                        raise RuntimeError(f"Lỗi từ Gemini API: {err_msg}")

                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    last_error = f"Lỗi kết nối tới Gemini ({current_model}): {e.__class__.__name__}: {e}"
                    logger.warning(last_error)
                    continue

        raise RuntimeError(
            "Không thể sinh văn bản từ Google Gemini sau khi thử các model. "
            f"Lỗi cuối: {last_error}"
        )
