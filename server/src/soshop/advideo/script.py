"""The review script: short spoken beats with a gesture and a product cue each.

The LLM answers in JSON. Everything it returns is validated and clamped, and
a deterministic template from the product facts stands in when the LLM is
unavailable, so a video can always be made.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx

from soshop.agent.live_prompts import clean_spoken_text, format_price
from soshop_server.core.config import settings

logger = logging.getLogger("soshop.advideo")

GESTURES = ("nod", "tilt", "emphasis", "aside", "smile", "engage")
"""Talk gestures the director knows (``soshop.agent.lipsync.motion.TALK_GESTURES``)."""

_GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
_MIN_BEATS = 3
_MAX_BEATS = 8
_MAX_BEAT_WORDS = 24
_HOOK_TITLE_CHARS = 40
_CTA_CHARS = 16
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

_SYSTEM_VI = (
    "Bạn là biên kịch video TikTok review sản phẩm. Người dẫn nói thẳng vào camera, "
    "giọng tự nhiên và thân mật như người thật, câu ngắn, dễ nghe. "
    'Chỉ trả về JSON đúng dạng: {"hook_title": "...", "beats": [{"text": "...", '
    '"gesture": "...", "show_product": false}], "cta": "..."}. '
    "Quy tắc: 5 đến 7 beat, mỗi beat là một câu nói từ 6 đến 16 từ. "
    "Beat đầu là câu hỏi hoặc câu gây tò mò để giữ người xem trong 3 giây đầu. "
    "Các beat giữa nêu lợi ích cụ thể lấy từ thông tin sản phẩm và ai nên dùng. "
    "Beat cuối nhắc giá (nếu có) và kêu gọi bấm mua. "
    "hook_title là chữ hiện trên màn hình ở đầu video, tối đa 32 ký tự, có thể dùng số. "
    "cta là chữ trên nút mua, tối đa 14 ký tự, ví dụ 'Mua ngay'. "
    "gesture chọn một trong: nod (gật đầu khẳng định), tilt (nghiêng đầu thân thiện), "
    "emphasis (nhướng mày nhấn mạnh), aside (quay sang bên như chỉ vào sản phẩm), "
    "smile (cười tươi), engage (nói thẳng vào camera). "
    "show_product là true cho một hoặc hai beat nói về hình thức hay công dụng nổi bật. "
    "Chỉ dùng thông tin được cung cấp: không bịa thành phần, số liệu, khuyến mãi, và "
    "không kể trải nghiệm cá nhân không có thật. Trong text, viết giá và số bằng chữ "
    "như khi đọc thành tiếng. Không emoji, không markdown."
)
_SYSTEM_EN = (
    "You write TikTok product review videos. The host talks straight to camera in a "
    "warm, natural voice, short easy sentences. Answer with JSON only, exactly: "
    '{"hook_title": "...", "beats": [{"text": "...", "gesture": "...", '
    '"show_product": false}], "cta": "..."}. Rules: 5 to 7 beats, each one spoken '
    "sentence of 6 to 16 words. The first beat is a question or a curiosity hook for "
    "the first 3 seconds. Middle beats give concrete benefits from the product facts "
    "and who it suits. The last beat mentions the price if known and asks viewers to "
    "buy. hook_title is on-screen text for the opening, at most 32 characters. cta is "
    "the buy button label, at most 14 characters. gesture is one of: nod, tilt, "
    "emphasis, aside, smile, engage. show_product is true for one or two beats about "
    "the product's look or standout use. Use only the facts given: never invent "
    "ingredients, numbers, promotions or personal experiences. No emoji or markdown."
)


class ScriptError(RuntimeError):
    """The LLM could not produce a usable script."""


@dataclass(frozen=True)
class ProductBrief:
    """What the script and the edit know about the product."""

    name: str
    price: Decimal | float | None = None
    currency: str = "VND"
    description: str = ""
    highlights: str = ""
    image: str | None = None
    """Local path or http(s) URL of a product photo, shown in the video."""

    def price_text(self, language: str) -> str | None:
        if self.price is None:
            return None
        return format_price(float(self.price), self.currency, language)


@dataclass(frozen=True)
class Beat:
    text: str
    gesture: str = "engage"
    show_product: bool = False


@dataclass(frozen=True)
class AdScript:
    hook_title: str
    beats: tuple[Beat, ...]
    cta: str
    language: str = "vi"
    meta: dict[str, Any] = field(default_factory=dict, compare=False)

    def to_json(self) -> dict[str, Any]:
        return {
            "hook_title": self.hook_title,
            "cta": self.cta,
            "language": self.language,
            "beats": [
                {"text": b.text, "gesture": b.gesture, "show_product": b.show_product}
                for b in self.beats
            ],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any], language: str = "vi") -> AdScript:
        """Validate and clamp a script from the LLM or a file. Raises ``ScriptError``."""
        beats: list[Beat] = []
        for raw in data.get("beats") or []:
            if not isinstance(raw, dict):
                continue
            text = _clip_words(clean_spoken_text(str(raw.get("text") or "")), _MAX_BEAT_WORDS)
            if not text:
                continue
            gesture = str(raw.get("gesture") or "").strip().lower()
            beats.append(
                Beat(
                    text=text,
                    gesture=gesture if gesture in GESTURES else "engage",
                    show_product=bool(raw.get("show_product")),
                )
            )
        if len(beats) < _MIN_BEATS:
            raise ScriptError(f"script has {len(beats)} usable beats, need {_MIN_BEATS}")
        beats = beats[:_MAX_BEATS]
        default_cta = "Mua ngay" if language == "vi" else "Shop now"
        hook = _one_line(str(data.get("hook_title") or ""))[:_HOOK_TITLE_CHARS].strip()
        cta = _one_line(str(data.get("cta") or ""))[:_CTA_CHARS].strip() or default_cta
        return cls(
            hook_title=hook or beats[0].text[:_HOOK_TITLE_CHARS],
            beats=tuple(beats),
            cta=cta,
            language=str(data.get("language") or language),
        )


def _one_line(text: str) -> str:
    return " ".join(text.replace("*", "").replace("#", "").split())


def _clip_words(text: str, limit: int) -> str:
    words = text.split()
    return " ".join(words[:limit])


def parse_script(content: str, language: str = "vi") -> AdScript:
    """Parse the LLM reply: drop reasoning blocks, take the outermost JSON object."""
    text = _THINK_RE.sub("", content)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ScriptError("the LLM reply holds no JSON object")
    try:
        data = json.loads(text[start : end + 1])
    except ValueError as exc:
        raise ScriptError(f"the LLM reply is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ScriptError("the LLM reply is not a JSON object")
    return AdScript.from_json(data, language)


def _brief(product: ProductBrief, language: str) -> str:
    labels = (
        ("Tên", "Giá", "Mô tả", "Điểm nổi bật")
        if language == "vi"
        else ("Name", "Price", "Description", "Highlights")
    )
    values = (product.name, product.price_text(language), product.description, product.highlights)
    return "\n".join(
        f"{label}: {value}" for label, value in zip(labels, values, strict=True) if value
    )


async def write_script(product: ProductBrief, language: str = "vi") -> AdScript:
    """Ask the LLM for a script. Raises ``ScriptError`` on any failure."""
    if not settings.GROQ_API_KEY:
        raise ScriptError("GROQ_API_KEY is not configured")
    payload = {
        "model": settings.GROQ_LLM_MODEL,
        "max_completion_tokens": 1400,
        "temperature": 0.8,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _SYSTEM_VI if language == "vi" else _SYSTEM_EN},
            {"role": "user", "content": _brief(product, language)},
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                _GROQ_CHAT_URL,
                json=payload,
                headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}"},
            )
    except httpx.HTTPError as exc:
        raise ScriptError(f"LLM request failed: {exc}") from exc
    if resp.status_code != 200:
        raise ScriptError(f"LLM returned {resp.status_code}: {resp.text[:200]}")
    try:
        content = str(resp.json()["choices"][0]["message"]["content"])
    except (KeyError, IndexError, ValueError) as exc:
        raise ScriptError("LLM reply has no content") from exc
    return parse_script(content, language)


def fallback_script(product: ProductBrief, language: str = "vi") -> AdScript:
    """A plain script from the product facts alone, for when the LLM is down."""
    price = product.price_text(language)
    points = [p.strip(" .-•") for p in re.split(r"[\n;•]+", product.highlights) if p.strip(" .-•")]
    if language == "vi":
        beats = [Beat(f"Cả nhà đã biết tới {product.name} chưa?", "emphasis")]
        if product.description:
            beats.append(Beat(product.description.split(".")[0].strip() + ".", "engage", True))
        beats += [Beat(f"Điểm mình thích là {p.lower()}.", "nod") for p in points[:3]]
        closing = f"Giá chỉ {price} thôi, " if price else ""
        beats.append(Beat(f"{closing}bấm vào giỏ hàng để đặt ngay nha.", "smile"))
        cta = "Mua ngay"
    else:
        beats = [Beat(f"Have you heard about {product.name}?", "emphasis")]
        if product.description:
            beats.append(Beat(product.description.split(".")[0].strip() + ".", "engage", True))
        beats += [Beat(f"What I like is {p.lower()}.", "nod") for p in points[:3]]
        closing = f"It's only {price}, " if price else ""
        beats.append(Beat(f"{closing}tap the cart to get yours.", "smile"))
        cta = "Shop now"
    while len(beats) < _MIN_BEATS:
        beats.insert(1, Beat(product.name + ".", "tilt", True))
    if not any(b.show_product for b in beats):
        beats[1] = Beat(beats[1].text, beats[1].gesture, True)
    return AdScript(product.name[:_HOOK_TITLE_CHARS], tuple(beats), cta, language)
