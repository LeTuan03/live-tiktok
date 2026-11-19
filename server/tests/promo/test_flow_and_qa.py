"""Flow driver helpers that need no browser, speech matching, and the bridge protocol."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from soshop.promo.bridge import BridgeError, FlowBridge
from soshop.promo.flow import (
    FLOW_MODELS,
    credit_ceiling,
    find_model,
    generation_request,
    parse_permission,
)
from soshop.promo.qa import match_score


def test_parse_permission_reads_vietnamese_and_english_quotes() -> None:
    vi = "Bạn có muốn tôi bắt đầu tạo 1 video này với chi phí là 10 tín dụng không? Phê duyệt"
    en = "Do you want me to generate 2 videos for 48 credits?"
    assert parse_permission(vi) == (10, 1)
    assert parse_permission(en) == (48, 2)
    assert parse_permission("Phê duyệt") == (None, None)


def test_model_catalog_and_ceilings() -> None:
    assert find_model("veo 3.1 - lite") is FLOW_MODELS[0]
    assert find_model("Sora") is None
    assert credit_ceiling("Omni 1.2 Flash") == 24
    assert credit_ceiling("unknown model") == 30
    retiring = {m.label for m in FLOW_MODELS if m.retiring}
    assert retiring == {"Veo 3.1 - Fast", "Veo 3.1 - Quality"}


def test_generation_request_names_model_references_and_keeps_prompt() -> None:
    text = generation_request("PROMPT BODY", "Veo 3.1 - Lite")
    assert "exactly one 8-second vertical 9:16 video" in text
    assert "Veo 3.1 - Lite" in text
    assert "first attached image is the person" in text
    assert text.endswith("PROMPT BODY")


def test_match_score_is_order_aware_and_tolerant() -> None:
    line = "Môi khô nứt mùa lạnh? Mình luôn mang thỏi son dưỡng này trong túi đó!"
    heard = "Môi khô nứt mùa lạnh Mình luôn mang thỏi son dưỡng này trong túi đó Há há há"
    assert match_score(line, heard) == 1.0
    assert match_score(line, "xin chào các bạn") < 0.2
    # 5 of the 15 scripted words were heard.
    assert match_score(line, "Môi khô nứt mùa lạnh") == pytest.approx(5 / 15, abs=0.001)
    assert match_score("", "anything") == 1.0


def test_match_score_ignores_number_spelling_tone_marks_and_small_typos() -> None:
    # Measured on a real Flow clip: the host said the line correctly, Whisper
    # wrote the price in digits, dropped an "l" and heard "nhá" for "nhé".
    line = "Chỉ năm mươi chín nghìn đồng mình đã có luôn Labello Classic nhấn vào để mua nhé"
    heard = "Chỉ 59.000 đồng mình đã có luôn Labelo Classic. Nhấn vào để mua nhá."
    # Only "nhé"/"nhá" counts against it: a short word with a different vowel.
    assert match_score(line, heard) == pytest.approx(12 / 13, abs=0.001)
    # A different product name is still a miss.
    assert match_score("mua Labello Classic", "mua Nivea Classic") == pytest.approx(
        2 / 3, abs=0.001
    )


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)


async def test_bridge_call_round_trip_and_errors() -> None:
    bridge = FlowBridge()
    with pytest.raises(BridgeError, match="Chưa kết nối"):
        await bridge.call("ping")

    socket = FakeSocket()
    bridge._ws = socket  # type: ignore[assignment]
    call = asyncio.create_task(bridge.call("tabs.list", {"x": 1}, timeout=2.0))
    await asyncio.sleep(0)
    request = socket.sent[0]
    assert (request["method"], request["params"]) == ("tabs.list", {"x": 1})
    bridge._dispatch({"id": request["id"], "result": [{"id": 7}]})
    assert await call == [{"id": 7}]

    failing = asyncio.create_task(bridge.call("cdp", timeout=2.0))
    await asyncio.sleep(0)
    bridge._dispatch({"id": socket.sent[1]["id"], "error": "tab 3 is not a Google Flow tab"})
    with pytest.raises(BridgeError, match="not a Google Flow tab"):
        await failing

    with pytest.raises(BridgeError, match="không phản hồi"):
        await bridge.call("ping", timeout=0.05)


def test_bridge_routes_events_to_listeners() -> None:
    bridge = FlowBridge()
    seen: list[dict[str, Any]] = []
    bridge.add_listener(seen.append)
    bridge._dispatch({"event": "hello", "version": "0.1.0"})
    bridge._dispatch({"event": "detached", "tabId": 3})
    assert bridge.hello == {"event": "hello", "version": "0.1.0"}
    assert [e["event"] for e in seen] == ["hello", "detached"]
    bridge.remove_listener(seen.append)
    bridge._dispatch({"event": "cdp"})
    assert len(seen) == 2
