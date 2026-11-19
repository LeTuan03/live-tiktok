"""Unit tests for the PitchTools auto-show timer + deferred LLM call.

The README's "Applied behavioral psychology" frame requires the in-app
product card to materialise *several seconds into the pitch* — not at
hook time, and not "maybe never" when the LLM forgets to call
``signal_product_pitch``. These tests pin both halves of that contract:

* The auto-show task fires the show envelope after ``auto_show_delay_s``
  even when the LLM tool is never called.
* An over-eager LLM call (before the delay window elapses) defers to the
  scheduled show instead of firing immediately.
* Calls after the auto-show has fired are idempotent no-ops.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from soshop.agent.schemas import PopupPayload
from soshop.agent.tools import PRODUCT_PITCH_TOPIC, PitchTools


def _payload() -> PopupPayload:
    return PopupPayload(
        id="00000000-0000-0000-0000-000000000004",
        title="Earbuds",
        image_url="https://example/p.jpg",
        cta="Tap to shop · $129",
        deep_link_url="https://example/d",
        landing_url="https://example/l",
    )


class _CapturingSendText:
    """Captures the (payload, topic) pairs the tool forwards on."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, payload: str, topic: str) -> None:
        self.calls.append((payload, topic))


@pytest.mark.asyncio
async def test_auto_show_fires_after_delay_without_llm_call() -> None:
    """The scheduled task emits the show envelope without any LLM call.

    Uses a sub-millisecond delay so the test stays fast. Real production
    delay (``AUTO_SHOW_DELAY_S``) is 2.5s; the value is dependency-injected
    here precisely so this test doesn't have to wait that long.
    """
    sender = _CapturingSendText()
    tools = PitchTools(
        product_id="prod-1",
        popup_payload=_payload(),
        send_text=sender,
        auto_show_delay_s=0.02,
    )
    tools.schedule_auto_show()
    # Wait long enough that the asyncio.sleep inside the task elapses.
    await asyncio.sleep(0.1)

    assert len(sender.calls) == 1, sender.calls
    payload, topic = sender.calls[0]
    assert topic == PRODUCT_PITCH_TOPIC
    body: dict[str, Any] = json.loads(payload)
    assert body["type"] == "show"
    assert body["product_id"] == "prod-1"
    assert body["popup"]["title"] == "Earbuds"
    tools.cleanup()


@pytest.mark.asyncio
async def test_llm_signal_before_delay_defers_to_auto_show() -> None:
    """An over-eager LLM call doesn't fire the card prematurely.

    Simulates the failure the user observed: the LLM calls
    ``signal_product_pitch`` at hook time. The server defers, the
    scheduled timer fires later, and the wire receives exactly one show.
    """
    sender = _CapturingSendText()
    tools = PitchTools(
        product_id="prod-1",
        popup_payload=_payload(),
        send_text=sender,
        auto_show_delay_s=0.05,
    )
    tools.schedule_auto_show()

    # LLM fires "immediately" (before the 50ms window elapses).
    result = await tools.signal_product_pitch.__wrapped__(tools, None)  # type: ignore[attr-defined]
    assert result == "deferred to scheduled show"
    # No envelope on the wire yet — the deferral is server-internal.
    assert sender.calls == []

    # Let the scheduled task elapse; the show fires from the timer.
    await asyncio.sleep(0.15)
    assert len(sender.calls) == 1
    tools.cleanup()


@pytest.mark.asyncio
async def test_llm_signal_after_delay_fires_immediately() -> None:
    """A late LLM call (post-delay, pre-auto-show race) fires synchronously."""
    sender = _CapturingSendText()
    tools = PitchTools(
        product_id="prod-1",
        popup_payload=_payload(),
        send_text=sender,
        auto_show_delay_s=10.0,  # Long enough the timer won't fire in this test.
    )
    tools.schedule_auto_show()
    # Manually rewind the rebind clock so the elapsed check passes.
    tools._rebind_at -= 100.0  # noqa: SLF001 — direct manipulation is the test seam.

    result = await tools.signal_product_pitch.__wrapped__(tools, None)  # type: ignore[attr-defined]
    assert result == "popup shown"
    assert len(sender.calls) == 1
    body = json.loads(sender.calls[0][0])
    assert body["type"] == "show"
    tools.cleanup()


@pytest.mark.asyncio
async def test_rebind_cancels_pending_auto_show() -> None:
    """A rebind before the timer fires cancels the prior schedule.

    Channel rotation rebinds the tools on every swap. A pending auto-show
    from the previous product must not fire under the new product's id.
    """
    sender = _CapturingSendText()
    tools = PitchTools(
        product_id="prod-1",
        popup_payload=_payload(),
        send_text=sender,
        auto_show_delay_s=0.05,
    )
    tools.schedule_auto_show()
    # Rebind to a different product before the timer fires.
    other = PopupPayload(
        id="00000000-0000-0000-0000-000000000005",
        title="Serum",
        image_url="https://example/s.jpg",
        cta="Tap · $38",
        deep_link_url="https://example/sd",
        landing_url="https://example/sl",
    )
    tools.rebind(product_id="prod-2", popup_payload=other)

    await asyncio.sleep(0.15)
    # Nothing fires — the new pitch hasn't scheduled its own auto-show
    # yet (the caller does that after kickoff).
    assert sender.calls == []
    tools.cleanup()


@pytest.mark.asyncio
async def test_auto_show_noop_when_no_popup_configured() -> None:
    """Freestyle dispatches (no popup) get no auto-show."""
    sender = _CapturingSendText()
    tools = PitchTools(
        product_id=None,
        popup_payload=None,
        send_text=sender,
        auto_show_delay_s=0.02,
    )
    tools.schedule_auto_show()
    await asyncio.sleep(0.1)
    assert sender.calls == []
    tools.cleanup()


@pytest.mark.asyncio
async def test_emit_dismiss_auto_sends_envelope_when_shown() -> None:
    """The orchestrator's natural-completion dismiss fires the envelope.

    Pins the Issue-3 fix path: server-driven dismiss after the
    SpeechHandle resolves naturally, so the client's compact pill
    clears before the next ad's auto-show fires.
    """
    sender = _CapturingSendText()
    tools = PitchTools(
        product_id="prod-1",
        popup_payload=_payload(),
        send_text=sender,
        auto_show_delay_s=0.02,
    )
    tools.schedule_auto_show()
    await asyncio.sleep(0.1)
    # One show on the wire.
    assert len(sender.calls) == 1

    emitted = await tools.emit_dismiss_auto()
    assert emitted is True
    # Now show + dismiss on the wire.
    assert len(sender.calls) == 2
    body = json.loads(sender.calls[1][0])
    assert body["type"] == "dismiss"
    assert body["product_id"] == "prod-1"
    tools.cleanup()


@pytest.mark.asyncio
async def test_emit_dismiss_auto_noop_when_show_never_fired() -> None:
    """Dismiss is suppressed when no show has gone out.

    Otherwise a viewer who never saw a card would receive a dismiss
    envelope, which is at best wasted bandwidth and at worst a hint
    to client-side state machines that something cleared.
    """
    sender = _CapturingSendText()
    tools = PitchTools(
        product_id="prod-1",
        popup_payload=_payload(),
        send_text=sender,
        auto_show_delay_s=100.0,  # show never fires under test latency
    )
    tools.schedule_auto_show()  # schedules; never elapses

    emitted = await tools.emit_dismiss_auto()
    assert emitted is False
    assert sender.calls == []
    tools.cleanup()


@pytest.mark.asyncio
async def test_emit_dismiss_auto_is_idempotent() -> None:
    """A second dismiss is a no-op even after the first emits.

    Shared idempotency state with ``signal_pitch_end`` (the LLM-callable
    tool): whoever fires first wins. Re-firing would race the next
    ad's show envelope.
    """
    sender = _CapturingSendText()
    tools = PitchTools(
        product_id="prod-1",
        popup_payload=_payload(),
        send_text=sender,
        auto_show_delay_s=0.02,
    )
    tools.schedule_auto_show()
    await asyncio.sleep(0.1)

    first = await tools.emit_dismiss_auto()
    second = await tools.emit_dismiss_auto()
    assert first is True
    assert second is False
    # Wire saw exactly one show + one dismiss.
    assert len(sender.calls) == 2
    tools.cleanup()


@pytest.mark.asyncio
async def test_emit_dismiss_auto_noop_when_no_product_configured() -> None:
    """Freestyle dispatches get no dismiss either."""
    sender = _CapturingSendText()
    tools = PitchTools(
        product_id=None,
        popup_payload=None,
        send_text=sender,
        auto_show_delay_s=0.02,
    )
    emitted = await tools.emit_dismiss_auto()
    assert emitted is False
    assert sender.calls == []
    tools.cleanup()
