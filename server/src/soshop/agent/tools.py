"""LLM-callable tools for the SoShop agent.

These tools let the persona drive the in-call pitch popup on the client
via a LiveKit text-stream topic.

The popup payload is fixed at dispatch time (see `DispatchMetadata`); the
tools just gate *when* the show/dismiss signal goes out, based on where the
LLM is in the pitch script.

Tool schemas are intentionally **zero-argument**. The dispatch-time
``product_id`` binding is authoritative — the LLM does not need to echo
it back, and every required string parameter is another chance for the
Groq function-call serializer to emit malformed JSON, which Groq
surfaces as ``APIError: Failed to call a function`` and livekit-agents
re-raises as a fatal ``APIConnectionError`` that aborts the in-flight
TTS stream (= audio cuts out mid-pitch). Zero-arg tools eliminate that
failure mode.

Show timing is **server-driven, not LLM-driven**. When a pitch starts
(at dispatch and after every in-place swap) the caller invokes
:meth:`PitchTools.schedule_auto_show`, which queues a background task
to fire the show payload after ``AUTO_SHOW_DELAY_S`` seconds. The LLM
tool ``signal_product_pitch`` is still wired so the prompt's beat
structure stays intact, but its behavior is now an idempotent
acknowledgement — calls before the delay has elapsed defer to the
scheduled show, and calls after the show has fired are no-ops. The
deterministic timer is what the README's "Applied behavioral
psychology" frame depends on: the card materialises *several seconds
into the pitch*, not at hook time and not "maybe never" when the LLM
forgets to call the tool.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable

from livekit.agents import llm

# `RunContext` must be importable at runtime, not behind TYPE_CHECKING.
# LiveKit's tool-schema builder calls `typing.get_type_hints(func)` on
# the @function_tool methods, and on Python 3.14 that eagerly resolves
# annotation strings — `RunContext` missing from module globals surfaces
# as `NameError: name 'RunContext' is not defined`, which the inference
# task wraps as `APIConnectionError: Connection error.` (misleading).
from livekit.agents.voice.events import RunContext

from soshop.agent.schemas import PopupPayload

__all__ = ["AUTO_SHOW_DELAY_S", "PRODUCT_PITCH_TOPIC", "PitchTools"]

logger = logging.getLogger("soshop.agent.tools")

PRODUCT_PITCH_TOPIC = "product_pitch"

SendText = Callable[[str, str], Awaitable[None]]

AUTO_SHOW_DELAY_S = 2.5
"""Seconds between a pitch starting and the product card materialising.

The behavioral-psychology target (README "Applied behavioral psychology
design") is that the host names the product and lands a hook *before*
the card appears — the viewer briefly imagines the product, then the
visual confirms it. ~2.5s lands roughly mid-hook at ElevenLabs'
~150 WPM. Tune by editing this constant; the client's compact-pill
collapse is a fixed 3s after the show fires.
"""


class PitchTools:
    """LLM tools for delivering and dismissing the in-call product pitch popup.

    Idempotent per dispatch: a single show fires (whether from the
    server-side auto-show timer or a late LLM tool call), and a single
    dismiss fires. The dispatch-time ``product_id`` binding is
    authoritative — the tool signatures take no arguments so the LLM
    cannot ship a hallucinated id, and Groq's function-call serializer
    never has a chance to emit malformed JSON for a required string
    parameter (Groq's ``tool_use_failed`` surfaces as a fatal mid-stream
    ``APIConnectionError`` that aborts the TTS chain — the audio-cut
    bug this schema fixes).
    """

    def __init__(
        self,
        *,
        product_id: str | None,
        popup_payload: PopupPayload | None,
        send_text: SendText,
        auto_show_delay_s: float = AUTO_SHOW_DELAY_S,
    ) -> None:
        self._product_id = product_id
        self._popup_payload = popup_payload
        self._send_text = send_text
        self._shown = False
        self._dismissed = False
        self._auto_show_delay_s = auto_show_delay_s
        self._auto_show_task: asyncio.Task[None] | None = None
        self._rebind_at: float = time.monotonic()

    def rebind(
        self,
        *,
        product_id: str | None,
        popup_payload: PopupPayload | None,
    ) -> None:
        """Re-target the tools at a new product/popup without rebuilding.

        Called by the agent's in-place persona-swap handler so a single
        ``PitchTools`` instance survives across personas in a feed
        session. The ``@llm.function_tool`` descriptors close over
        ``self`` and read these fields lazily at call time, so updating
        them is honored by the LLM tool registry.

        Per-dispatch idempotency state is also reset so the new
        persona's pitch can fire its show / dismiss exactly once. Any
        pending auto-show task from the previous segment is cancelled —
        the caller is expected to call :meth:`schedule_auto_show` again
        once the new persona's opening line has been kicked off.
        """
        self._cancel_auto_show_task()
        self._product_id = product_id
        self._popup_payload = popup_payload
        self._shown = False
        self._dismissed = False
        self._rebind_at = time.monotonic()

    def schedule_auto_show(self) -> None:
        """Queue the deterministic show fire for this pitch.

        Called by the entrypoint and the swap path immediately after the
        new persona's ``generate_reply`` is kicked off. Waits
        :data:`AUTO_SHOW_DELAY_S` seconds, then emits the show payload
        on the ``product_pitch`` topic regardless of whether the LLM
        called :meth:`signal_product_pitch`. Idempotent: a pending task
        from a prior schedule is cancelled before the new one starts.

        No-ops when no popup is configured (freestyle dispatch). Safe
        to call multiple times — only the latest schedule survives.
        """
        self._cancel_auto_show_task()
        if self._popup_payload is None or self._product_id is None:
            return
        try:
            self._auto_show_task = asyncio.create_task(self._auto_show_runner())
        except RuntimeError:
            # No running event loop (e.g. unit-test construction path).
            # Caller can always retry from within an async context.
            logger.warning(
                "schedule_auto_show: no running event loop; skipping",
                extra={"product_id": self._product_id},
            )

    async def _auto_show_runner(self) -> None:
        """Background task body for the deterministic show fire."""
        try:
            await asyncio.sleep(self._auto_show_delay_s)
        except asyncio.CancelledError:
            return
        try:
            await self._emit_show(source="auto_show")
        except Exception:
            logger.exception("auto_show: emit failed")

    def _cancel_auto_show_task(self) -> None:
        task = self._auto_show_task
        if task is not None and not task.done():
            task.cancel()
        self._auto_show_task = None

    async def _emit_show(self, *, source: str) -> bool:
        """Send the show envelope if not already shown. Returns True on emit."""
        if self._popup_payload is None or self._product_id is None:
            return False
        if self._shown:
            return False
        message = json.dumps(
            {
                "type": "show",
                "product_id": self._product_id,
                "popup": self._popup_payload.model_dump(),
            }
        )
        await self._send_text(message, PRODUCT_PITCH_TOPIC)
        self._shown = True
        logger.info(
            "pitch_show_fired",
            extra={"source": source, "product_id": self._product_id},
        )
        return True

    def cleanup(self) -> None:
        """Cancel any pending auto-show task. Safe to call repeatedly."""
        self._cancel_auto_show_task()

    async def emit_dismiss_auto(self) -> bool:
        """Fire the dismiss envelope from the server. Returns True on emit.

        Called by the channel orchestrator after a pitch finishes
        speaking naturally (``CompletionOutcome.reason == "natural"``)
        so the client's compact-pill dismisses before the next ad's
        auto-show fires. No-ops when:

        * the show envelope was never fired (nothing to dismiss);
        * a dismiss has already gone out for this product;
        * no product is bound (freestyle dispatch).

        Distinct from :meth:`signal_pitch_end` so the existing LLM-
        callable tool stays registered (chat-history cleanup + test
        harness depend on its presence) while the server-driven path
        is its own thing. The two paths share idempotency state — the
        first call that lands wins, the second is a no-op.
        """
        if self._popup_payload is None or self._product_id is None:
            return False
        if not self._shown:
            return False
        if self._dismissed:
            return False
        message = json.dumps({"type": "dismiss", "product_id": self._product_id})
        try:
            await self._send_text(message, PRODUCT_PITCH_TOPIC)
        except Exception:
            logger.exception("emit_dismiss_auto: send failed")
            return False
        self._dismissed = True
        logger.info(
            "pitch_dismiss_fired",
            extra={"source": "auto_dismiss", "product_id": self._product_id},
        )
        return True

    @llm.function_tool(
        description=(
            "Acknowledge that the pitch has reached the beat to surface the "
            "product card. The server schedules the card on a deterministic "
            "timer a few seconds into the pitch, so this tool is primarily a "
            "safety net — call it once after you've named the product and "
            "given a hook. If you call it too early (before the host has "
            "introduced the product), the server defers to the scheduled "
            "show. Takes no arguments."
        )
    )
    async def signal_product_pitch(self, ctx: RunContext) -> str:
        """Deliver the popup_payload over the product_pitch topic.

        Behavior matrix:
        - No popup configured → no-op, returns a status string.
        - Already shown (by the auto-show timer or a prior call) → no-op.
        - Called before ``AUTO_SHOW_DELAY_S`` has elapsed since the last
          rebind → defer to the scheduled show, no-op now (the timer
          will fire). This prevents an over-eager LLM from raising the
          card at hook time before the viewer has even heard the
          product name.
        - Otherwise → emit the show envelope immediately.
        """
        if self._popup_payload is None or self._product_id is None:
            logger.warning("signal_product_pitch: no popup configured for this dispatch")
            return "no popup configured for this dispatch"
        if self._shown:
            return "popup already shown"
        elapsed = time.monotonic() - self._rebind_at
        if elapsed < self._auto_show_delay_s:
            logger.info(
                "signal_product_pitch deferred",
                extra={
                    "elapsed_s": round(elapsed, 3),
                    "min_delay_s": self._auto_show_delay_s,
                    "product_id": self._product_id,
                },
            )
            return "deferred to scheduled show"
        await self._emit_show(source="llm_tool")
        return "popup shown"

    @llm.function_tool(
        description=(
            "Dismiss the product pitch popup. Call this when you've "
            "finished the pitch and are moving on, so the popup "
            "auto-closes. Takes no arguments."
        )
    )
    async def signal_pitch_end(self, ctx: RunContext) -> str:
        """Send a dismiss signal on the product_pitch topic."""
        if self._product_id is None:
            logger.info("signal_pitch_end: no product bound; ignoring")
            return "no product to dismiss"
        if self._dismissed:
            logger.info(
                "signal_pitch_end: already dismissed for %s — ignoring duplicate",
                self._product_id,
            )
            return "popup already dismissed"
        message = json.dumps({"type": "dismiss", "product_id": self._product_id})
        await self._send_text(message, PRODUCT_PITCH_TOPIC)
        self._dismissed = True
        logger.info("signal_pitch_end fired for %s", self._product_id)
        return "popup dismissed"
