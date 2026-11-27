"""The product card burned into the restream while a product video plays.

A pushed stream carries nothing but the picture, so the price and the basket
number have to be in it. The card follows the on-air page's look
(``web/components/broadcast/OnAirView.tsx``): white and rounded, the product
photo, the name, the price in red and a red pill, here "Giỏ hàng số N" with N
the product's place in the playlist. It is drawn once per product with Pillow
and laid over the picture by every feeder (:class:`soshop.restream.encoder.Overlay`).

Placement keeps clear of the platform's own controls: ``top`` sits under
TikTok's LIVE header (host, viewer count), ``middle`` ends just above the
comment stream that covers the lower third.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from soshop.promo.assemble import bold_font

Placement = Literal["top", "middle"]

_DESIGN_W = 720
"""Sizes below are for a 720 px wide frame and scale with the frame."""
_TOP_Y = 0.135
_MIDDLE_BOTTOM = 0.60
_WIDTH = 0.82
_MARGIN = 24
_PAD = 16
_PHOTO = 112
_GAP = 14
_NAME_PT, _PRICE_PT, _PILL_PT = 30, 40, 24
_SHADOW = 14
_INK = (23, 23, 23, 255)
_RED = (220, 38, 38, 255)
_WHITE = (255, 255, 255, 255)
_CARD = (255, 255, 255, 245)
_ELLIPSIS = "…"


class CardError(RuntimeError):
    """The card cannot be drawn (no font with Vietnamese glyphs on this machine)."""


@dataclass(frozen=True)
class ProductCard:
    """What the card says about the product on air."""

    name: str
    price: str | None
    number: int | None
    """The product's place in the playlist, which the basket should match."""
    image_url: str | None = None

    @property
    def cta(self) -> str:
        return f"Giỏ hàng số {self.number}" if self.number else "Mua ngay"


def _font(size: float) -> ImageFont.FreeTypeFont:
    font = bold_font(max(round(size), 8))
    if not isinstance(font, ImageFont.FreeTypeFont):
        raise CardError("Không có font hỗ trợ tiếng Việt (đặt ADVIDEO_FONT tới một file .ttf).")
    return font


def lines(text: str, font: ImageFont.FreeTypeFont, width: float, most: int) -> list[str]:
    """``text`` wrapped to ``width`` in at most ``most`` lines, the last cut with "…"."""
    out: list[str] = []
    for word in text.split():
        if out and font.getlength(f"{out[-1]} {word}") <= width:
            out[-1] = f"{out[-1]} {word}"
        else:
            out.append(word)
    if not out:
        return []
    cut = len(out) > most
    out = out[:most]
    last = out[-1]
    if cut or font.getlength(last) > width:
        while last and font.getlength(last + _ELLIPSIS) > width:
            last = last[:-1].rstrip()
        out[-1] = last + _ELLIPSIS
    return out


def _photo(path: Path | None, size: int, radius: int) -> Image.Image | None:
    if path is None or not path.is_file():
        return None
    try:
        with Image.open(path) as img:
            square = ImageOps.fit(ImageOps.exif_transpose(img).convert("RGBA"), (size, size))
    except (OSError, ValueError):
        return None
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    square.putalpha(mask)
    return square


def render(
    card: ProductCard,
    photo: Path | None,
    *,
    frame_w: int,
    frame_h: int,
    placement: Placement,
) -> tuple[Image.Image, int, int]:
    """The card as an RGBA image, and where its top-left corner goes in the frame."""
    u = frame_w / _DESIGN_W
    pad, shadow = round(_PAD * u), round(_SHADOW * u)
    card_w = round(frame_w * _WIDTH)
    pic = _photo(photo, round(_PHOTO * u), round(18 * u))
    text_x = pad + (pic.width + round(_GAP * u) if pic is not None else 0)
    text = _Text(card, u, card_w - text_x - pad)
    card_h = max(text.height, pic.height if pic is not None else 0) + 2 * pad

    image = Image.new("RGBA", (card_w + 2 * shadow, card_h + 2 * shadow), (0, 0, 0, 0))
    box = (shadow, shadow, shadow + card_w, shadow + card_h)
    radius = round(22 * u)
    shade = Image.new("RGBA", image.size, (0, 0, 0, 0))
    drop = round(4 * u)
    ImageDraw.Draw(shade).rounded_rectangle(
        (box[0], box[1] + drop, box[2], box[3] + drop), radius=radius, fill=(0, 0, 0, 90)
    )
    image.alpha_composite(shade.filter(ImageFilter.GaussianBlur(shadow / 2)))
    ImageDraw.Draw(image).rounded_rectangle(box, radius=radius, fill=_CARD)
    if pic is not None:
        image.alpha_composite(pic, (shadow + pad, shadow + (card_h - pic.height) // 2))
    text.draw(image, shadow + text_x, shadow + (card_h - text.height) // 2)

    if placement == "top":
        top = round(frame_h * _TOP_Y)
    else:
        top = round(frame_h * _MIDDLE_BOTTOM) - card_h
    return image, max(round(_MARGIN * u) - shadow, 0), max(top - shadow, 0)


class _Text:
    """The card's text column: up to two lines of name, then the price and the pill."""

    def __init__(self, card: ProductCard, u: float, width: int) -> None:
        self.card, self.u, self.width = card, u, width
        self.name_font, self.price_font = _font(_NAME_PT * u), _font(_PRICE_PT * u)
        self.pill_font = _font(_PILL_PT * u)
        self.gap = round(_GAP * u)
        self.names = lines(card.name, self.name_font, width, 2)
        self.name_h = round(self.name_font.size * 1.22)
        self.pill_pad = (round(14 * u), round(7 * u))
        self.pill_w = round(self.pill_font.getlength(card.cta)) + 2 * self.pill_pad[0]
        self.pill_h = round(self.pill_font.size * 1.25) + 2 * self.pill_pad[1]
        price_w = self.price_font.getlength(card.price) if card.price else 0.0
        self.row_h = max(round(self.price_font.size * 1.2) if card.price else 0, self.pill_h)
        self.pill_below = bool(card.price) and price_w + self.gap + self.pill_w > width
        below = self.pill_h + self.gap if self.pill_below else 0
        self.height = len(self.names) * self.name_h + round(6 * u) + self.row_h + below

    def draw(self, image: Image.Image, x: int, y: int) -> None:
        draw = ImageDraw.Draw(image)
        for line in self.names:
            draw.text((x, y), line, font=self.name_font, fill=_INK)
            y += self.name_h
        y += round(6 * self.u)
        if self.card.price:
            price_y = y + (self.row_h - self.price_font.size) // 2 - round(3 * self.u)
            draw.text((x, price_y), self.card.price, font=self.price_font, fill=_RED)
        if self.pill_below:
            pill_x, pill_y = x, y + self.row_h + self.gap
        else:
            pill_x = x + self.width - self.pill_w if self.card.price else x
            pill_y = y + (self.row_h - self.pill_h) // 2
        right, bottom = pill_x + self.pill_w, pill_y + self.pill_h
        draw.rounded_rectangle((pill_x, pill_y, right, bottom), radius=self.pill_h // 2, fill=_RED)
        middle = ((pill_x + right) / 2, (pill_y + bottom) / 2)
        draw.text(middle, self.card.cta, font=self.pill_font, fill=_WHITE, anchor="mm")


def write(
    card: ProductCard,
    photo: Path | None,
    out: Path,
    *,
    frame_w: int,
    frame_h: int,
    placement: Placement,
) -> tuple[int, int]:
    """Save the card as the PNG ``out``; where it goes in the frame."""
    image, x, y = render(card, photo, frame_w=frame_w, frame_h=frame_h, placement=placement)
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out, "PNG")
    return x, y
