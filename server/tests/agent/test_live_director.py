"""Behavior of the live host's director loop.

The director is pure orchestration, so it runs here against an in-memory
speaker and store. Each test scripts studio commands at specific turns and
asserts on what the host was asked to say and how product statuses moved.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from soshop.agent.live_director import (
    Command,
    LiveDirector,
    LiveProduct,
    Mode,
    Pacing,
    ProductStatus,
    SpeakResult,
)
from soshop.agent.live_prompts import PRODUCT_BEATS, HostProfile, ProductFacts

PROFILE = HostProfile(name="Lan", intro="", personality="", language="vi", notes="")
FAST = Pacing(chat_gap=(0, 0), product_gap=(0, 0), heartbeat=3600, failure_backoff=(0,))


def _product(pid: str, status: ProductStatus = "pending") -> LiveProduct:
    return LiveProduct(
        id=pid,
        status=status,
        facts=ProductFacts(
            name=f"Sản phẩm {pid}",
            price=100000,
            currency="VND",
            description="",
            highlights="",
            script="",
        ),
    )


class FakeStore:
    def __init__(self, products: list[LiveProduct]) -> None:
        self.products = {p.id: p for p in products}
        self.order = [p.id for p in products]
        self.states: list[tuple[Mode, str | None]] = []
        self.live = True

    async def list_products(self) -> list[LiveProduct]:
        return [self.products[i] for i in self.order if i in self.products]

    async def set_product_status(self, product_id: str, status: ProductStatus) -> None:
        current = self.products[product_id]
        self.products[product_id] = LiveProduct(current.id, status, current.facts)

    async def reset_products(self) -> None:
        for product_id in list(self.products):
            await self.set_product_status(product_id, "pending")

    async def publish_state(
        self, *, mode: Mode, current_product_id: str | None, last_line: str | None
    ) -> None:
        self.states.append((mode, current_product_id))

    async def is_live(self) -> bool:
        return self.live

    def statuses(self) -> dict[str, str]:
        return {i: self.products[i].status for i in self.order if i in self.products}


class FakeSpeaker:
    """Records cues. ``on_turn[n]`` runs just before turn ``n`` is spoken."""

    def __init__(self) -> None:
        self.cues: list[str] = []
        self.instructions: list[str] = []
        self.on_turn: dict[int, Callable[[], None]] = {}
        self.outcomes: dict[int, SpeakResult] = {}

    async def set_instructions(self, instructions: str) -> None:
        self.instructions.append(instructions)

    async def speak(self, cue: str, interrupt: asyncio.Event) -> SpeakResult:
        index = len(self.cues)
        self.cues.append(cue)
        hook = self.on_turn.get(index)
        if hook is not None:
            hook()
        await asyncio.sleep(0)
        if interrupt.is_set():
            return SpeakResult("interrupted")
        return self.outcomes.get(index, SpeakResult("spoken", f"line {index}"))


def _director(store: FakeStore, speaker: FakeSpeaker) -> LiveDirector:
    return LiveDirector(
        speaker=speaker, store=store, profile=PROFILE, pacing=FAST, rng=lambda a, _b: a
    )


async def _run(director: LiveDirector) -> None:
    await asyncio.wait_for(director.run(), timeout=5)


def _product_turns(speaker: FakeSpeaker, name: str) -> int:
    return sum(1 for cue in speaker.cues if f"Tên: Sản phẩm {name}" in cue)


@pytest.mark.asyncio
async def test_opens_with_greeting_then_chats_until_ended() -> None:
    store, speaker = FakeStore([]), FakeSpeaker()
    director = _director(store, speaker)
    speaker.on_turn[3] = lambda: director.submit(Command("end"))

    await _run(director)

    assert "Phiên live vừa bắt đầu" in speaker.cues[0]
    assert all("Trò chuyện tự do" in cue for cue in speaker.cues[1:3])
    assert "chào tạm biệt" in speaker.cues[-1]
    assert store.states[-1][0] == "ending"


@pytest.mark.asyncio
async def test_presents_products_in_order_then_returns_to_chat() -> None:
    store, speaker = FakeStore([_product("a"), _product("b")]), FakeSpeaker()
    director = _director(store, speaker)
    speaker.on_turn[0] = lambda: director.submit(Command("start_products"))
    # opening + 2 products x beats + "all done" turn, then one chat turn.
    end_at = 1 + 2 * PRODUCT_BEATS + 2
    speaker.on_turn[end_at] = lambda: director.submit(Command("end"))

    await _run(director)

    product_cues = [c for c in speaker.cues if "Giới thiệu sản phẩm" in c]
    assert len(product_cues) == 2 * PRODUCT_BEATS
    assert all("Sản phẩm a" in c for c in product_cues[:PRODUCT_BEATS])
    assert all("Sản phẩm b" in c for c in product_cues[PRODUCT_BEATS:])
    assert store.statuses() == {"a": "done", "b": "done"}
    assert "giới thiệu xong tất cả sản phẩm" in speaker.cues[1 + 2 * PRODUCT_BEATS]
    assert director.mode == "ending"


@pytest.mark.asyncio
async def test_skip_marks_current_product_and_moves_on() -> None:
    store, speaker = FakeStore([_product("a"), _product("b")]), FakeSpeaker()
    director = _director(store, speaker)
    speaker.on_turn[0] = lambda: director.submit(Command("start_products"))
    speaker.on_turn[2] = lambda: director.submit(Command("skip"))  # during a's 2nd beat
    speaker.on_turn[2 + 1 + PRODUCT_BEATS + 1] = lambda: director.submit(Command("end"))

    await _run(director)

    assert store.statuses() == {"a": "skipped", "b": "done"}
    assert _product_turns(speaker, "b") == PRODUCT_BEATS


@pytest.mark.asyncio
async def test_pause_holds_until_resume_and_repeats_the_interrupted_beat() -> None:
    store, speaker = FakeStore([_product("a")]), FakeSpeaker()
    director = _director(store, speaker)
    speaker.on_turn[0] = lambda: director.submit(Command("start_products"))
    speaker.on_turn[2] = lambda: director.submit(Command("pause"))

    task = asyncio.create_task(director.run())
    for _ in range(50):
        await asyncio.sleep(0)
    assert director.mode == "paused"
    turns_while_paused = len(speaker.cues)
    for _ in range(50):
        await asyncio.sleep(0)
    assert len(speaker.cues) == turns_while_paused  # silent while paused

    speaker.on_turn[turns_while_paused + PRODUCT_BEATS] = lambda: director.submit(Command("end"))
    director.submit(Command("resume"))
    await asyncio.wait_for(task, timeout=5)

    # The beat cut by the pause is spoken again, so the product still gets
    # every beat delivered in full.
    assert _product_turns(speaker, "a") == PRODUCT_BEATS + 1
    assert store.statuses() == {"a": "done"}


@pytest.mark.asyncio
async def test_present_jumps_to_a_product_and_requeues_the_interrupted_one() -> None:
    store, speaker = FakeStore([_product("a"), _product("b"), _product("c")]), FakeSpeaker()
    director = _director(store, speaker)
    speaker.on_turn[0] = lambda: director.submit(Command("start_products"))
    speaker.on_turn[1] = lambda: director.submit(Command("present", product_id="c"))
    # After c completes the queue resumes from the top: a, then b.
    speaker.on_turn[2 + 3 * PRODUCT_BEATS] = lambda: director.submit(Command("end"))

    await _run(director)

    presented = [
        name for cue in speaker.cues for name in ("a", "b", "c") if f"Tên: Sản phẩm {name}" in cue
    ]
    assert presented[0] == "a"  # the turn that got interrupted
    assert presented[1 : 1 + PRODUCT_BEATS] == ["c"] * PRODUCT_BEATS
    assert presented[1 + PRODUCT_BEATS : 1 + 2 * PRODUCT_BEATS] == ["a"] * PRODUCT_BEATS
    assert store.statuses() == {"a": "done", "b": "done", "c": "done"}


@pytest.mark.asyncio
async def test_replaying_a_finished_product_presents_it_again() -> None:
    store, speaker = FakeStore([_product("a", "done")]), FakeSpeaker()
    director = _director(store, speaker)
    speaker.on_turn[0] = lambda: director.submit(Command("present", product_id="a"))
    speaker.on_turn[1 + PRODUCT_BEATS] = lambda: director.submit(Command("end"))

    await _run(director)

    assert _product_turns(speaker, "a") == PRODUCT_BEATS
    assert store.statuses() == {"a": "done"}


@pytest.mark.asyncio
async def test_start_products_reruns_the_list_when_everything_already_aired() -> None:
    store, speaker = FakeStore([_product("a", "done"), _product("b", "skipped")]), FakeSpeaker()
    director = _director(store, speaker)
    speaker.on_turn[0] = lambda: director.submit(Command("start_products"))
    speaker.on_turn[1 + 2 * PRODUCT_BEATS] = lambda: director.submit(Command("end"))

    await _run(director)

    assert _product_turns(speaker, "a") == PRODUCT_BEATS
    assert _product_turns(speaker, "b") == PRODUCT_BEATS


@pytest.mark.asyncio
async def test_stop_products_returns_to_chat_and_requeues_current() -> None:
    store, speaker = FakeStore([_product("a")]), FakeSpeaker()
    director = _director(store, speaker)
    speaker.on_turn[0] = lambda: director.submit(Command("start_products"))
    speaker.on_turn[1] = lambda: director.submit(Command("stop_products"))
    speaker.on_turn[3] = lambda: director.submit(Command("end"))

    await _run(director)

    assert store.statuses() == {"a": "pending"}
    assert "Trò chuyện tự do" in speaker.cues[2]


@pytest.mark.asyncio
async def test_viewer_comment_is_answered_by_name_on_the_next_turn() -> None:
    store, speaker = FakeStore([]), FakeSpeaker()
    director = _director(store, speaker)
    speaker.on_turn[0] = lambda: director.add_comment("Tuấn", "Shop có freeship không?")
    speaker.on_turn[2] = lambda: director.submit(Command("end"))

    await _run(director)

    assert "[BÌNH LUẬN] Tuấn: Shop có freeship không?" in speaker.cues[1]
    assert "Trả lời các bình luận" in speaker.cues[1]


@pytest.mark.asyncio
async def test_comment_lost_to_a_failed_turn_is_retried() -> None:
    store, speaker = FakeStore([]), FakeSpeaker()
    director = _director(store, speaker)
    speaker.on_turn[0] = lambda: director.add_comment("Mai", "Còn hàng không ạ?")
    speaker.outcomes[1] = SpeakResult("failed")
    speaker.on_turn[3] = lambda: director.submit(Command("end"))

    await _run(director)

    assert "Mai: Còn hàng không ạ?" in speaker.cues[1]
    assert "Mai: Còn hàng không ạ?" in speaker.cues[2]


@pytest.mark.asyncio
async def test_say_is_spoken_without_interrupting_the_current_turn() -> None:
    store, speaker = FakeStore([]), FakeSpeaker()
    director = _director(store, speaker)
    speaker.on_turn[1] = lambda: director.submit(Command("say", text="Còn 5 phút khuyến mãi"))
    speaker.on_turn[3] = lambda: director.submit(Command("end"))

    await _run(director)

    assert "Trò chuyện tự do" in speaker.cues[1]  # the turn in flight completed
    assert "Còn 5 phút khuyến mãi" in speaker.cues[2]


@pytest.mark.asyncio
async def test_product_deleted_mid_presentation_advances_to_the_next() -> None:
    store, speaker = FakeStore([_product("a"), _product("b")]), FakeSpeaker()
    director = _director(store, speaker)
    speaker.on_turn[0] = lambda: director.submit(Command("start_products"))

    def _delete_a() -> None:
        del store.products["a"]

    speaker.on_turn[1] = _delete_a
    speaker.on_turn[2 + PRODUCT_BEATS] = lambda: director.submit(Command("end"))

    await _run(director)

    assert store.statuses() == {"b": "done"}


@pytest.mark.asyncio
async def test_catalog_edits_refresh_the_system_prompt() -> None:
    store, speaker = FakeStore([_product("a")]), FakeSpeaker()
    director = _director(store, speaker)
    speaker.on_turn[0] = lambda: director.submit(Command("start_products"))

    def _add_b() -> None:
        store.products["b"] = _product("b")
        store.order.append("b")

    speaker.on_turn[1] = _add_b
    speaker.on_turn[1 + 2 * PRODUCT_BEATS] = lambda: director.submit(Command("end"))

    await _run(director)

    assert "Sản phẩm b" not in speaker.instructions[0]
    assert "Sản phẩm b" in speaker.instructions[-1]
    assert store.statuses() == {"a": "done", "b": "done"}


@pytest.mark.asyncio
async def test_stops_when_the_session_is_no_longer_live() -> None:
    store, speaker = FakeStore([]), FakeSpeaker()
    director = LiveDirector(
        speaker=speaker,
        store=store,
        profile=PROFILE,
        pacing=Pacing(chat_gap=(0, 0), product_gap=(0, 0), heartbeat=0.01, failure_backoff=(0,)),
        rng=lambda a, _b: a,
    )
    store.live = False

    await _run(director)

    assert director.mode == "ending"


def test_unknown_command_is_rejected() -> None:
    director = _director(FakeStore([]), FakeSpeaker())
    assert director.submit(Command("dance")) is False
    assert director.submit(Command("pause")) is True
