"""The promo script: one spoken line plus staging per 8-second Veo shot.

Veo renders each shot as one continuous 8 s clip with native audio, so a shot
carries everything the prompt needs: what the host physically does with the
product, the framing, the emotion, and the exact line she says. Visual fields
are written in English (Veo follows English staging best); the dialogue is in
the host's language.

The LLM answers in JSON; everything is validated here so a bad answer is
caught before Flow credits are spent.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from soshop.agent.live_prompts import format_price
from soshop_server.core.config import settings

logger = logging.getLogger("soshop.promo.script")

Language = Literal["vi", "en"]
Beat = Literal["hook", "demo", "benefit", "cta"]

MIN_SHOTS = 2
MAX_SHOTS = 4
SHOT_SECONDS = 8
MAX_DIALOGUE_WORDS: dict[str, int] = {"vi": 24, "en": 21}
"""About 8 s of relaxed speech, leaving room to breathe at the start and end."""
MIN_DIALOGUE_WORDS = 5

_GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_SPACES_RE = re.compile(r"\s+")


class ScriptError(RuntimeError):
    """The LLM could not produce a usable script."""


class PromoProduct(BaseModel):
    """What the script and the prompts know about the product (a snapshot)."""

    id: str
    name: str
    brand: str = ""
    category: str = ""
    price: float | None = None
    currency: str = "VND"
    description: str = ""
    highlights: list[str] = Field(default_factory=list)
    usage: str = ""
    talking_points: str = ""
    """What the shop wants said: promotions, gifts, guarantees."""
    image_url: str | None = None


class PromoHost(BaseModel):
    """The AI model presenting the product."""

    id: str | None = None
    name: str
    language: Language = "vi"
    image_url: str | None = None
    personality: str = ""


class Shot(BaseModel):
    beat: Beat
    action: str = Field(min_length=10, max_length=600)
    camera: str = Field(min_length=3, max_length=200)
    emotion: str = Field(min_length=3, max_length=120)
    dialogue: str = Field(min_length=3, max_length=400)


class PromoScript(BaseModel):
    angle: str = Field(max_length=300)
    product_look: str = Field(max_length=400)
    setting: str = Field(max_length=300)
    shots: list[Shot] = Field(min_length=MIN_SHOTS, max_length=MAX_SHOTS)
    caption: str = Field(default="", max_length=600)


# ------------------------------------------------------------------- prompt

_SYSTEM = """You are the creative director of short vertical product videos (TikTok, Reels) \
in which one AI-generated female host presents a single product to camera. Each video is \
{shots} shots of exactly 8 seconds, generated one by one by a video model (Veo) from your \
staging and line. Answer with JSON only, exactly this shape:
{{"angle": "...", "product_look": "...", "setting": "...", "caption": "...",
  "shots": [{{"beat": "hook|demo|benefit|cta", "action": "...", "camera": "...", \
"emotion": "...", "dialogue": "..."}}]}}

Rules:
- angle: one English sentence, the creative idea of the video.
- product_look: English, how the product physically looks and is held (form factor, size \
relative to a hand, packaging type). Infer it from the facts; never invent brand text.
- setting: English, one believable location that suits the product (bathroom vanity, \
bright kitchen, living room, gym...). The same setting for every shot.
- shots: exactly {shots}. Shot 1 beat "hook", the last shot beat "cta", the ones between \
"demo" or "benefit". The product is visible and in the host's hands in every shot.
- action (English, 1 to 3 sentences): what the host physically does with the product during \
those 8 seconds, concrete and realistic for this product: holding it up to the lens, \
opening it, applying or using it the way it is really used, showing the result. One \
continuous action per shot, no cuts, no other people, no text or graphics.
- camera (English): framing and movement, e.g. "medium close-up, handheld selfie, slight \
push-in".
- emotion (English): facial expression and energy, e.g. "curious raised eyebrows, then a \
delighted smile".
- dialogue ({language_name}): exactly what she says in that shot, {min_words} to {max_words} \
words, natural spoken {language_name} like a real creator talking to friends, and it must \
match the action of the same shot. Write prices and numbers as words, the way they are \
spoken. No quotation marks, no emoji, no hashtags.
- In action, call the host "she", never by name.
- The hook line grabs attention in the first 2 seconds. The cta line says the price if it \
is known, exactly the amount given (never rounded), and asks viewers to tap to buy.
- caption ({language_name}): one short post caption with up to 3 hashtags.
- Use ONLY the product facts given. Never invent ingredients, specs, numbers, awards, \
discounts or personal results that are not in the facts. No claims of instant or timed \
results ("in seconds", "after 3 days") and no medical promises unless the facts say so; \
describe what the product does in the words of the facts."""

_LANGUAGE_NAME = {"vi": "Vietnamese", "en": "English"}


def product_facts(product: PromoProduct, language: str) -> str:
    """The product facts block handed to the LLM."""
    lines = [f"Name: {product.name}"]
    if product.brand:
        lines.append(f"Brand: {product.brand}")
    if product.category:
        lines.append(f"Category: {product.category}")
    price = format_price(product.price, product.currency, language)
    if price:
        lines.append(f"Price (say exactly this amount, spelled out in words): {price}")
    if product.description:
        lines.append(f"Description: {product.description}")
    if product.highlights:
        lines.append("Key features:\n" + "\n".join(f"- {h}" for h in product.highlights))
    if product.usage:
        lines.append(f"How it is used: {product.usage}")
    if product.talking_points.strip():
        lines.append(f"What the shop wants mentioned: {product.talking_points.strip()}")
    return "\n".join(lines)


def build_messages(
    product: PromoProduct, host: PromoHost, shots: int, language: str
) -> list[dict[str, str]]:
    system = _SYSTEM.format(
        shots=shots,
        language_name=_LANGUAGE_NAME.get(language, "English"),
        min_words=MIN_DIALOGUE_WORDS + 3,
        max_words=MAX_DIALOGUE_WORDS.get(language, 21) - 4,
    )
    user = f"Host: {host.name}"
    if host.personality:
        user += f" ({host.personality})"
    user += f"\n\nProduct facts:\n{product_facts(product, language)}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# --------------------------------------------------------------- validation


def _clean(text: Any) -> str:
    """One line, no double quotes (they delimit the dialogue in the Veo prompt)."""
    value = str(text or "").replace('"', "").replace("“", "").replace("”", "")
    return _SPACES_RE.sub(" ", value).strip()


def word_count(text: str) -> int:
    return len(text.split())


_INSTANT_CLAIMS = (
    "ngay lập tức",
    "tức thì",
    "trong vài giây",
    "chỉ sau một đêm",
    "sau một đêm",
    "instantly",
    "in seconds",
    "overnight",
    "immediately",
)
"""Overpromises the LLM likes to add; allowed only when the facts say them."""


def unsupported_claim(dialogue: str, facts: str) -> str | None:
    """The first instant-result phrase in ``dialogue`` that ``facts`` do not contain."""
    said, known = dialogue.lower(), facts.lower()
    return next((p for p in _INSTANT_CLAIMS if p in said and p not in known), None)


def parse_script(raw: str, shots: int, language: str, facts: str = "") -> PromoScript:
    """Validate the LLM's JSON. Raises ``ScriptError`` with a reason the LLM can fix."""
    text = _THINK_RE.sub("", raw).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ScriptError("the answer is not a JSON object")
    try:
        data = json.loads(text[start : end + 1])
    except ValueError as exc:
        raise ScriptError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("shots"), list):
        raise ScriptError("missing the shots list")
    raw_shots = data["shots"]
    if len(raw_shots) != shots:
        raise ScriptError(f"expected exactly {shots} shots, got {len(raw_shots)}")
    limit = MAX_DIALOGUE_WORDS.get(language, 21)
    cleaned: list[dict[str, str]] = []
    for index, item in enumerate(raw_shots):
        if not isinstance(item, dict):
            raise ScriptError(f"shot {index + 1} is not an object")
        shot = {k: _clean(item.get(k)) for k in ("beat", "action", "camera", "emotion", "dialogue")}
        shot["beat"] = _normalise_beat(shot["beat"], index, shots)
        words = word_count(shot["dialogue"])
        if words < MIN_DIALOGUE_WORDS or words > limit:
            raise ScriptError(
                f"shot {index + 1} dialogue has {words} words; it must be "
                f"{MIN_DIALOGUE_WORDS} to {limit} to fit 8 seconds"
            )
        claim = unsupported_claim(shot["dialogue"], facts)
        if claim:
            raise ScriptError(
                f'shot {index + 1} dialogue promises "{claim}", which the product facts do '
                "not support; describe what the product does instead"
            )
        cleaned.append(shot)
    try:
        return PromoScript(
            angle=_clean(data.get("angle"))[:300],
            product_look=_clean(data.get("product_look"))[:400],
            setting=_clean(data.get("setting"))[:300] or "a bright, tidy home studio",
            caption=str(data.get("caption") or "").strip()[:600],
            shots=[Shot.model_validate(s) for s in cleaned],
        )
    except ValueError as exc:
        raise ScriptError(f"a field is missing or too short: {exc}") from exc


def _normalise_beat(beat: str, index: int, total: int) -> str:
    if index == 0:
        return "hook"
    if index == total - 1:
        return "cta"
    return beat if beat in ("demo", "benefit") else "demo"


def validate_edit(script: PromoScript, language: str) -> PromoScript:
    """Re-check a script edited by hand in the studio before it costs credits."""
    limit = MAX_DIALOGUE_WORDS.get(language, 21)
    shots = []
    for index, shot in enumerate(script.shots):
        dialogue = _clean(shot.dialogue)
        if word_count(dialogue) > limit:
            raise ScriptError(
                f"Lời thoại cảnh {index + 1} dài {word_count(dialogue)} từ, tối đa {limit} từ "
                "để vừa 8 giây."
            )
        shots.append(
            shot.model_copy(
                update={
                    "dialogue": dialogue,
                    "action": _clean(shot.action),
                    "camera": _clean(shot.camera),
                    "emotion": _clean(shot.emotion),
                }
            )
        )
    return script.model_copy(update={"shots": shots, "setting": _clean(script.setting)})


# ---------------------------------------------------------------------- LLM


async def _chat(client: httpx.AsyncClient, messages: list[dict[str, str]]) -> str:
    model = settings.PROMO_LLM_MODEL
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.8,
        "max_completion_tokens": 3000,
        "response_format": {"type": "json_object"},
    }
    if model.startswith("openai/gpt-oss"):
        payload["reasoning_effort"] = "medium"
    resp = await client.post(
        _GROQ_CHAT_URL,
        json=payload,
        headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}"},
    )
    if resp.status_code == 429:
        raise ScriptError("LLM đang bị giới hạn tốc độ. Hãy thử lại sau ít giây.")
    if resp.status_code != 200:
        logger.warning("promo script LLM error %s: %s", resp.status_code, resp.text[:400])
        raise ScriptError(f"LLM trả về lỗi {resp.status_code}.")
    try:
        return str(resp.json()["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, ValueError) as exc:
        raise ScriptError("LLM trả về dữ liệu không hợp lệ.") from exc


async def generate_script(
    product: PromoProduct, host: PromoHost, shots: int = 3, language: str | None = None
) -> PromoScript:
    """Ask the LLM for a script; up to two corrective retries when validation fails."""
    if not settings.GROQ_API_KEY:
        raise ScriptError("GROQ_API_KEY chưa được cấu hình trên server.")
    shots = max(MIN_SHOTS, min(MAX_SHOTS, shots))
    lang = language or host.language
    base = build_messages(product, host, shots, lang)
    facts = product_facts(product, lang)
    mistakes: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            for _attempt in range(3):
                # A fresh request each time: replaying a rejected answer made the
                # model drift (one retry came back with 22 shots).
                messages = [dict(m) for m in base]
                if mistakes:
                    messages[-1]["content"] += "\n\nAvoid these mistakes: " + "; ".join(mistakes)
                content = await _chat(client, messages)
                try:
                    return parse_script(content, shots, lang, facts)
                except ScriptError as exc:
                    mistakes.append(str(exc))
                    logger.info("promo script rejected: %s", exc)
    except httpx.HTTPError as exc:
        raise ScriptError(f"Không gọi được dịch vụ LLM: {exc}") from exc
    raise ScriptError(f"LLM chưa viết được kịch bản hợp lệ ({mistakes[-1]}). Hãy thử lại.")
