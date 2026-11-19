"""Promo script validation and Veo prompt building (no network)."""

from __future__ import annotations

import json

import pytest

from soshop.promo.prompts import shot_prompt
from soshop.promo.script import (
    PromoHost,
    PromoProduct,
    PromoScript,
    ScriptError,
    Shot,
    build_messages,
    parse_script,
    product_facts,
    unsupported_claim,
    validate_edit,
)

PRODUCT = PromoProduct(
    id="p1",
    name="Son dưỡng môi",
    brand="Labello",
    price=59000,
    currency="VND",
    description="Son dưỡng dạng thỏi.",
    highlights=["Dưỡng ẩm cho môi khô", "Nhỏ gọn"],
    usage="Thoa trực tiếp lên môi.",
)
HOST = PromoHost(name="Mai", language="vi", personality="vui vẻ")


def _answer(shots: list[dict[str, str]], **extra: str) -> str:
    return json.dumps(
        {
            "angle": "A pocket lip balm.",
            "product_look": "a slim blue stick",
            "setting": "bathroom vanity.",
            "caption": "Môi mềm #son",
            "shots": shots,
            **extra,
        },
        ensure_ascii=False,
    )


def _shot(beat: str, dialogue: str) -> dict[str, str]:
    return {
        "beat": beat,
        "action": "She holds the lip balm up to the lens and smiles.",
        "camera": "medium close-up",
        "emotion": "bright smile",
        "dialogue": dialogue,
    }


GOOD_LINES = [
    "Môi khô nứt mùa lạnh thì làm sao đây mọi người?",
    "Mở nắp, thoa nhẹ một lượt là môi mềm hơn hẳn luôn nè.",
    "Chỉ năm mươi chín nghìn đồng thôi, bấm mua ngay nhé.",
]


def test_parse_script_accepts_a_valid_answer_and_cleans_it() -> None:
    shots = [
        _shot("demo", GOOD_LINES[0]),
        _shot("benefit", GOOD_LINES[1]),
        _shot("x", GOOD_LINES[2]),
    ]
    shots[1]["dialogue"] = '"' + GOOD_LINES[1] + '"'
    script = parse_script("<think>plan</think>" + _answer(shots), 3, "vi")
    # First and last beats are forced; quotes are stripped from the dialogue.
    assert [s.beat for s in script.shots] == ["hook", "benefit", "cta"]
    assert '"' not in script.shots[1].dialogue
    assert script.setting == "bathroom vanity."


def test_parse_script_rejects_wrong_shot_count() -> None:
    with pytest.raises(ScriptError, match="exactly 3 shots"):
        parse_script(_answer([_shot("hook", GOOD_LINES[0])]), 3, "vi")


def test_parse_script_rejects_dialogue_too_long_for_eight_seconds() -> None:
    long_line = " ".join(["chữ"] * 30)
    shots = [_shot("hook", long_line), _shot("cta", GOOD_LINES[2])]
    with pytest.raises(ScriptError, match="30 words"):
        parse_script(_answer(shots), 2, "vi")


def test_parse_script_rejects_instant_result_claims_absent_from_facts() -> None:
    shots = [
        _shot("hook", "Môi mềm ngay lập tức chỉ với một lần thoa thôi nha"),
        _shot("cta", GOOD_LINES[2]),
    ]
    with pytest.raises(ScriptError, match="ngay lập tức"):
        parse_script(_answer(shots), 2, "vi", facts=product_facts(PRODUCT, "vi"))
    # The same phrase is fine when the facts make that claim.
    assert unsupported_claim("mềm ngay lập tức", "Facts: mềm ngay lập tức") is None


def test_parse_script_rejects_non_json() -> None:
    with pytest.raises(ScriptError):
        parse_script("Sorry, I cannot help.", 3, "vi")


def test_product_facts_and_messages_carry_every_fact() -> None:
    facts = product_facts(PRODUCT, "vi")
    for fact in (
        "Son dưỡng môi",
        "Labello",
        "59.000 đồng",
        "Dưỡng ẩm cho môi khô",
        "Thoa trực tiếp",
    ):
        assert fact in facts
    system, user = build_messages(PRODUCT, HOST, 3, "vi")
    assert "exactly 3" in system["content"] and "Vietnamese" in system["content"]
    assert "Mai (vui vẻ)" in user["content"] and facts in user["content"]


def test_validate_edit_rejects_overlong_hand_edits() -> None:
    script = PromoScript(
        angle="",
        product_look="",
        setting="room",
        shots=[
            Shot(**_shot("hook", GOOD_LINES[0])),  # type: ignore[arg-type]
            Shot(**_shot("cta", " ".join(["từ"] * 25))),  # type: ignore[arg-type]
        ],
    )
    with pytest.raises(ScriptError, match="cảnh 2"):
        validate_edit(script, "vi")


def test_shot_prompt_pins_references_dialogue_and_bans_text() -> None:
    script = parse_script(
        _answer([_shot("hook", GOOD_LINES[0]), _shot("cta", GOOD_LINES[2])]), 2, "vi"
    )
    prompt = shot_prompt(script, script.shots[0], PRODUCT, HOST, "vi")
    assert f'"{GOOD_LINES[0]}"' in prompt
    assert "Labello Son dưỡng môi" in prompt
    assert "person reference image" in prompt and "product reference image" in prompt
    assert "in Vietnamese" in prompt and "No subtitles" in prompt
    assert ".." not in prompt
