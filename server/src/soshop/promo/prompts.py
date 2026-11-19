"""Veo prompts for each shot of a promo script.

Flow gets two reference images per shot (the host's portrait and the product
photo), so the prompt names them by role instead of describing the host's
face, and pins the product to its photo so Veo does not redesign the label.
"""

from __future__ import annotations

from soshop.promo.script import PromoHost, PromoProduct, PromoScript, Shot

_LANGUAGE_NAME = {"vi": "Vietnamese", "en": "English"}
_VOICE = {
    "vi": "a warm, bright young Vietnamese female voice with a natural Northern accent",
    "en": "a warm, bright young female voice",
}


def _sentence(text: str) -> str:
    """``text`` ending in exactly one full stop (or ``""``)."""
    text = text.strip().rstrip(".").strip()
    return f"{text}." if text else ""


def shot_prompt(
    script: PromoScript, shot: Shot, product: PromoProduct, host: PromoHost, language: str
) -> str:
    """The full Veo prompt for one 8-second shot."""
    language_name = _LANGUAGE_NAME.get(language, "English")
    product_name = product.name if not product.brand else f"{product.brand} {product.name}"
    look = _sentence(script.product_look)
    look = f" (roughly: {look[:-1]}; the reference image wins if they differ)" if look else ""
    return " ".join(
        [
            "Vertical 9:16 smartphone video, authentic TikTok product review, one continuous",
            "8-second shot, photorealistic, natural soft daylight, shallow depth of field.",
            f"Setting: {_sentence(script.setting)}",
            "Host: the young woman from the person reference image, same face, hair and",
            "outfit, looking into the lens like a real creator.",
            f"Product: the {product_name} from the product reference image{look}. Keep its",
            "exact shape, colors, packaging, label and logo; never add or change any text on",
            "it. It stays clearly visible in her hands.",
            f"Action: {_sentence(shot.action)}",
            f"Camera: {_sentence(shot.camera)}",
            f"Performance: {_sentence(shot.emotion)[:-1]}; natural facial expressions,",
            "expressive hand gestures and relaxed body movement, perfectly natural lip sync.",
            f"She says in {language_name}, in {_VOICE.get(language, _VOICE['en'])}:",
            f'"{shot.dialogue}"',
            "Audio: only her voice and soft room tone, no background music.",
            "No subtitles, no captions, no on-screen text, no watermark, no other people.",
        ]
    )


def all_prompts(
    script: PromoScript, product: PromoProduct, host: PromoHost, language: str
) -> list[str]:
    return [shot_prompt(script, s, product, host, language) for s in script.shots]
