"""Cue text and spoken-text cleaning for the live host."""

from __future__ import annotations

import pytest

from soshop.agent.live_prompts import (
    HostProfile,
    ProductFacts,
    ViewerComment,
    build_system_prompt,
    chat_cue,
    clean_spoken_chunk,
    clean_spoken_text,
    format_price,
    product_cue,
)

SERUM = ProductFacts(
    name="Serum vitamin C",
    price=320000,
    currency="VND",
    description="Làm sáng da",
    highlights="15% vitamin C",
    script="Mở đầu bằng câu hỏi về da xỉn màu",
)


@pytest.mark.parametrize(
    ("price", "currency", "language", "expected"),
    [
        (199000, "VND", "vi", "199.000 đồng"),
        (199000, "VND", "en", "199.000 VND"),
        (24.5, "USD", "en", "$24.5"),
        (24, "USD", "vi", "24 đô la Mỹ"),
        (1299.99, "EUR", "en", "1,299.99 EUR"),
        (None, "VND", "vi", ""),
    ],
)
def test_format_price(price: float | None, currency: str, language: str, expected: str) -> None:
    assert format_price(price, currency, language) == expected


def test_clean_spoken_text_strips_tags_markup_and_emoji() -> None:
    raw = "[ĐẠO DIỄN] **Chào** cả nhà 😍 #sale  nha!"
    assert clean_spoken_text(raw) == "Chào cả nhà sale nha!"


def test_clean_spoken_chunk_keeps_vietnamese_diacritics_and_punctuation() -> None:
    text = "Giá chỉ một trăm chín mươi chín nghìn đồng, quá hời!"
    assert clean_spoken_chunk(text) == text


def test_system_prompt_lists_catalog_and_private_cue_rules() -> None:
    profile = HostProfile("Lan", "Mê skincare", "Vui vẻ", "vi", "Shop Mộc Lan, freeship từ 300k")
    prompt = build_system_prompt(profile, [SERUM])

    assert "Bạn là Lan" in prompt
    assert "1. Serum vitamin C (320.000 đồng)" in prompt
    assert "Shop Mộc Lan" in prompt
    assert "[ĐẠO DIỄN]" in prompt and "[BÌNH LUẬN]" in prompt


def test_system_prompt_follows_model_language() -> None:
    prompt = build_system_prompt(HostProfile("Ava", "", "", "en", ""), [])
    assert "You are Ava" in prompt
    assert "[DIRECTOR]" in prompt


def test_product_cue_withholds_price_until_the_last_beat() -> None:
    first = product_cue("vi", SERUM, beat=0, index=1, total=3, comments=[])
    last = product_cue("vi", SERUM, beat=2, index=1, total=3, comments=[])

    assert "Chưa nói giá" in first
    assert "công bố giá" in last
    # Facts ride along on every beat because history is truncated hard.
    assert "320.000 đồng" in first and "15% vitamin C" in last


def test_product_cue_carries_viewer_comments() -> None:
    cue = product_cue(
        "vi",
        SERUM,
        beat=1,
        index=2,
        total=3,
        comments=[ViewerComment("Hà", "Da dầu dùng được không?")],
    )
    assert cue.startswith("[BÌNH LUẬN] Hà: Da dầu dùng được không?")
    assert "Giới thiệu sản phẩm 2 trên 3" in cue


def test_chat_cue_rotates_topics_and_prioritizes_comments() -> None:
    assert chat_cue("vi", 0, []) != chat_cue("vi", 1, [])
    answer = chat_cue("vi", 0, [ViewerComment("An", "Chào chị")])
    assert "An: Chào chị" in answer
    assert "Trả lời các bình luận" in answer


def test_history_cue_drops_the_fact_sheet_but_keeps_comments() -> None:
    from soshop.agent.live_prompts import history_cue

    cue = product_cue(
        "vi", SERUM, beat=0, index=1, total=2, comments=[ViewerComment("Hà", "Còn hàng không?")]
    )
    compact = history_cue(cue)

    assert compact.splitlines() == [
        "[BÌNH LUẬN] Hà: Còn hàng không?",
        "[ĐẠO DIỄN] Giới thiệu sản phẩm 1 trên 2.",
    ]
    assert history_cue(chat_cue("vi", 0, [])) == chat_cue("vi", 0, [])


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Chào cả nhà, hôm nay mình có món mới nha!", True),
        ("Giá chỉ 199.000 đồng (rẻ hơn 20%) thôi.", True),
        ('{"name": "signal", "arguments": {}}', False),
        ("Dạ <tool_call> something", False),
        ('"g": "rural"\n/parameter\n/function\n/toolcall', False),
        ("   ", False),
    ],
)
def test_looks_like_speech(text: str, expected: bool) -> None:
    from soshop.agent.live_prompts import looks_like_speech

    assert looks_like_speech(text) is expected
