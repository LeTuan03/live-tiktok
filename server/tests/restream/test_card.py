"""The product card drawn over the restream, and the playlist numbers on it."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from soshop.restream import card as cards
from soshop.restream.card import ProductCard, lines, render, write
from soshop.restream.plan import product_cards
from soshop.services.broadcasts import BroadcastItem, ItemProduct

try:
    FONT = cards._font(30)
except cards.CardError:  # pragma: no cover - a machine without any TrueType font
    FONT = None
needs_font = pytest.mark.skipif(FONT is None, reason="no TrueType font with Vietnamese")

CARD = ProductCard("Son kem lì Black Rouge màu A12", "189.000đ", 3, "/media/uploads/x.jpg")


def test_the_pill_names_the_basket_slot() -> None:
    assert CARD.cta == "Giỏ hàng số 3"
    assert ProductCard("Kem", None, None).cta == "Mua ngay"


@needs_font
def test_long_names_wrap_to_two_lines_and_end_with_an_ellipsis() -> None:
    assert FONT is not None
    width = 300
    assert lines("Kem", FONT, width, 2) == ["Kem"]
    assert lines("", FONT, width, 2) == []
    long = "Son kem lì siêu mịn môi lâu trôi Black Rouge Air Fit Velvet Tint màu A12 đỏ cam"
    got = lines(long, FONT, width, 2)
    assert len(got) == 2 and got[-1].endswith("…")
    assert all(FONT.getlength(line) <= width for line in got)
    one_word = lines("Siêuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuudài", FONT, width, 2)
    assert len(one_word) == 1 and one_word[0].endswith("…")


@needs_font
@pytest.mark.parametrize(("w", "h"), [(720, 1280), (1080, 1920)])
def test_the_card_fits_the_frame_where_the_placement_says(w: int, h: int, tmp_path: Path) -> None:
    photo = tmp_path / "p.jpg"
    Image.new("RGB", (640, 480), (200, 40, 40)).save(photo)
    top, tx, ty = render(CARD, photo, frame_w=w, frame_h=h, placement="top")
    mid, mx, my = render(CARD, None, frame_w=w, frame_h=h, placement="middle")
    for image, x, y in ((top, tx, ty), (mid, mx, my)):
        assert image.mode == "RGBA"
        assert 0 <= x and x + image.width <= w and 0 <= y and y + image.height <= h
        assert image.getpixel((0, 0))[3] == 0  # transparent around the card
    assert ty < h * 0.2  # under the platform's header
    assert my + mid.height <= h * 0.62  # above the comments
    assert top.width == mid.width  # same card width with or without a photo


@needs_font
def test_write_saves_a_png_and_returns_its_position(tmp_path: Path) -> None:
    out = tmp_path / "cards" / "c.png"
    x, y = write(CARD, tmp_path / "missing.jpg", out, frame_w=720, frame_h=1280, placement="top")
    with Image.open(out) as saved:
        assert saved.format == "PNG" and saved.mode == "RGBA"
    assert (x, y) == render(CARD, None, frame_w=720, frame_h=1280, placement="top")[1:]


def _item(item_id: str, product: ItemProduct | None) -> BroadcastItem:
    return BroadcastItem.model_construct(id=item_id, role="product", product=product)


def test_cards_number_products_by_their_place_in_the_playlist() -> None:
    son = ItemProduct(id="p1", name="Son", price=189000, currency="VND", image_url="/m/a.jpg")
    kem = ItemProduct(id="p2", name="Kem", price=None, currency="VND", image_url=None)
    got = product_cards([_item("a", son), _item("b", None), _item("c", kem)])
    assert got == {
        "a": ProductCard("Son", "189.000đ", 1, "/m/a.jpg"),
        "c": ProductCard("Kem", None, 3, None),  # the clip without a product still counts
    }
