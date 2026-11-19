"""LLM-written talking points for a live product.

The output is a set of talking points, not a teleprompter script: the live
host rephrases them on air (see ``soshop.agent.live_prompts``).
"""

from __future__ import annotations

import logging

import httpx

from soshop.agent.live_prompts import clean_spoken_text, format_price
from soshop.services.studio import ProductRow
from soshop_server.core.config import settings

logger = logging.getLogger("soshop.services.studio_script")

_GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"

_SYSTEM = {
    "vi": (
        "Bạn là biên kịch cho phiên livestream bán hàng. Viết gợi ý kịch bản giới thiệu "
        "sản phẩm bằng tiếng Việt, giọng thân mật như người bán hàng thật. "
        "Gồm đúng ba đoạn ngắn, mỗi đoạn một dòng: (1) câu dẫn dắt gây tò mò, "
        "(2) hai đến ba lợi ích chính và ai nên dùng, (3) giá và lời kêu gọi chốt đơn. "
        "Tổng cộng 60 đến 110 từ. Chỉ dùng thông tin được cung cấp, không bịa thành phần, "
        "khuyến mãi hay số liệu. Không dùng emoji, markdown hay tiêu đề."
    ),
    "en": (
        "You write for a live shopping stream. Draft talking points for the product in "
        "a warm, natural seller's voice. Exactly three short paragraphs, one per line: "
        "(1) a hook, (2) two or three key benefits and who it suits, (3) the price and a "
        "call to action. 60 to 110 words in total. Use only the facts given, never invent "
        "ingredients, promotions or numbers. No emoji, markdown or headings."
    ),
}


class ScriptGenerationError(RuntimeError):
    """The LLM could not produce a script (missing key, rate limit, outage)."""


def _product_brief(product: ProductRow, language: str) -> str:
    price = format_price(product.price, product.currency, language)
    labels = (
        ("Tên", "Giá", "Mô tả", "Điểm nổi bật")
        if language == "vi"
        else ("Name", "Price", "Description", "Highlights")
    )
    values = (product.name, price, product.description, product.highlights)
    return "\n".join(
        f"{label}: {value}" for label, value in zip(labels, values, strict=True) if value
    )


async def generate_product_script(product: ProductRow, language: str) -> str:
    if not settings.GROQ_API_KEY:
        raise ScriptGenerationError("GROQ_API_KEY chưa được cấu hình trên server.")
    payload = {
        "model": settings.GROQ_LLM_MODEL,
        "max_completion_tokens": 500,
        "temperature": 0.8,
        "messages": [
            {"role": "system", "content": _SYSTEM.get(language, _SYSTEM["en"])},
            {"role": "user", "content": _product_brief(product, language)},
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                _GROQ_CHAT_URL,
                json=payload,
                headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}"},
            )
    except httpx.HTTPError as exc:
        raise ScriptGenerationError(f"Không gọi được dịch vụ LLM: {exc}") from exc
    if resp.status_code == 429:
        raise ScriptGenerationError("LLM đang bị giới hạn tốc độ. Hãy thử lại sau ít giây.")
    if resp.status_code != 200:
        logger.warning("script generation failed: %s %s", resp.status_code, resp.text[:300])
        raise ScriptGenerationError(f"LLM trả về lỗi {resp.status_code}.")
    try:
        content = resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as exc:
        raise ScriptGenerationError("LLM trả về dữ liệu không hợp lệ.") from exc
    script = "\n".join(
        line for line in (clean_spoken_text(part) for part in str(content).splitlines()) if line
    )
    if not script:
        raise ScriptGenerationError("LLM trả về kịch bản rỗng.")
    return script
