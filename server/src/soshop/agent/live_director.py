"""The live host's director: decides what the AI model says next, turn by turn.

A livestream is one long unscripted session, so instead of a single prompt
the director runs a loop of short spoken turns:

* **chatting**: free talk (greeting, small talk, answering viewer comments).
* **presenting**: the ordered product queue, each product in a few turns
  (lead-in, detail, price + call to action), then the next pending product.
  When the queue is empty the host goes back to chatting.
* **paused**: silent until resumed.

The studio drives it with commands (start_products, pause, resume, skip,
present, stop_products, say, end). Commands that change direction interrupt
the current speech; the loop then picks up the new state on its next pass.

This module has no LiveKit or asyncpg imports. Speech goes through the
``Speaker`` protocol and persistence through ``LiveStore`` so the loop can be
unit-tested with in-memory fakes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from soshop.agent import live_prompts as prompts
from soshop.agent.live_prompts import HostProfile, ProductFacts, ViewerComment

logger = logging.getLogger("soshop.agent.live")

Mode = Literal["starting", "chatting", "presenting", "paused", "ending"]
SpeakOutcome = Literal["spoken", "interrupted", "failed"]
ProductStatus = Literal["pending", "presenting", "done", "skipped"]

COMMAND_ACTIONS = frozenset(
    {"start_products", "pause", "resume", "skip", "present", "stop_products", "say", "end"}
)
# Commands that must cut the current speech so the change lands immediately.
_INTERRUPTING = frozenset({"start_products", "pause", "skip", "present", "stop_products", "end"})

_MAX_COMMENTS_PER_TURN = 3
_MAX_PENDING_COMMENTS = 30
_FAILURE_BACKOFF_S = (6.0, 12.0, 20.0)


@dataclass(frozen=True)
class Command:
    action: str
    product_id: str | None = None
    text: str | None = None


@dataclass(frozen=True)
class SpeakResult:
    outcome: SpeakOutcome
    text: str | None = None


@dataclass(frozen=True)
class LiveProduct:
    id: str
    status: ProductStatus
    facts: ProductFacts


class Speaker(Protocol):
    async def speak(self, cue: str, interrupt: asyncio.Event) -> SpeakResult:
        """Speak one turn for ``cue``. Stops early when ``interrupt`` is set."""
        ...

    async def set_instructions(self, instructions: str) -> None: ...


class LiveStore(Protocol):
    async def list_products(self) -> list[LiveProduct]: ...

    async def set_product_status(self, product_id: str, status: ProductStatus) -> None: ...

    async def reset_products(self) -> None: ...

    async def publish_state(
        self, *, mode: Mode, current_product_id: str | None, last_line: str | None
    ) -> None: ...

    async def is_live(self) -> bool: ...


@dataclass(frozen=True)
class Pacing:
    """Pauses between turns, in seconds. Tests shrink these to zero."""

    # Kept short: generating the next turn (LLM, then first TTS audio) already
    # puts a few seconds of natural silence between turns.
    chat_gap: tuple[float, float] = (0.5, 1.5)
    product_gap: tuple[float, float] = (0.2, 0.6)
    heartbeat: float = 5.0
    failure_backoff: Sequence[float] = _FAILURE_BACKOFF_S


class LiveDirector:
    def __init__(
        self,
        *,
        speaker: Speaker,
        store: LiveStore,
        profile: HostProfile,
        pacing: Pacing | None = None,
        rng: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._speaker = speaker
        self._store = store
        self._profile = profile
        self._pacing = pacing or Pacing()
        self._rng = rng

        self.mode: Mode = "starting"
        self._resume_mode: Mode = "chatting"
        self.current_product_id: str | None = None
        self._beat = 0
        self._last_line: str | None = None
        self._ended = False

        self._commands: deque[Command] = deque()
        self._comments: deque[ViewerComment] = deque(maxlen=_MAX_PENDING_COMMENTS)
        self._say_queue: deque[str] = deque()
        self._interrupt = asyncio.Event()
        self._wake = asyncio.Event()
        self._topic_index = 0
        self._failures = 0
        self._catalog_signature: tuple[tuple[str, str, float | None], ...] | None = None

    # ------------------------------------------------------------- inputs

    def submit(self, command: Command) -> bool:
        """Queue a studio command. Returns False for an unknown action."""
        if command.action not in COMMAND_ACTIONS:
            return False
        self._commands.append(command)
        if command.action in _INTERRUPTING:
            self._interrupt.set()
        self._wake.set()
        return True

    def add_comment(self, name: str, text: str) -> None:
        name = prompts.clean_spoken_text(name)[:40] or "bạn xem"
        text = prompts.clean_spoken_text(text)[:300]
        if not text:
            return
        self._comments.append(ViewerComment(name=name, text=text))
        self._wake.set()

    # --------------------------------------------------------------- loop

    async def run(self) -> None:
        """Run until an ``end`` command arrives or the session stops being live."""
        heartbeat = asyncio.create_task(self._heartbeat(), name="live-heartbeat")
        try:
            await self._refresh_instructions(await self._store.list_products())
            await self._set_mode("chatting")
            await self._turn(prompts.opening_cue(self._profile.language))
            while not self._ended:
                await self._step()
            await self._set_mode("ending")
            self._interrupt.clear()
            await self._speaker.speak(prompts.goodbye_cue(self._profile.language), asyncio.Event())
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

    async def _step(self) -> None:
        await self._apply_commands()
        if self._ended:
            return
        if self.mode == "paused":
            await self._idle(None)
            return
        if self._say_queue:
            await self._turn(prompts.say_cue(self._profile.language, self._say_queue.popleft()))
            return
        if self.mode == "presenting":
            await self._present_step()
        else:
            await self._chat_step()

    # ----------------------------------------------------------- commands

    async def _apply_commands(self) -> None:
        while self._commands:
            command = self._commands.popleft()
            handler: Callable[[Command], Awaitable[None]] = getattr(self, f"_on_{command.action}")
            await handler(command)
        self._interrupt.clear()

    async def _on_start_products(self, _: Command) -> None:
        products = await self._store.list_products()
        if products and not any(p.status in ("pending", "presenting") for p in products):
            # Every product already aired: a fresh "start" runs the list again.
            await self._store.reset_products()
        if self.mode != "presenting":
            self._beat = 0
        await self._set_mode("presenting")

    async def _on_pause(self, _: Command) -> None:
        if self.mode != "paused":
            self._resume_mode = self.mode if self.mode != "starting" else "chatting"
            await self._set_mode("paused")

    async def _on_resume(self, _: Command) -> None:
        if self.mode == "paused":
            await self._set_mode(self._resume_mode)

    async def _on_skip(self, _: Command) -> None:
        presenting = self.mode == "presenting" or (
            self.mode == "paused" and self._resume_mode == "presenting"
        )
        if not presenting:
            return
        if self.current_product_id is not None:
            await self._store.set_product_status(self.current_product_id, "skipped")
        self.current_product_id = None
        self._beat = 0
        await self._set_mode("presenting")

    async def _on_present(self, command: Command) -> None:
        if not command.product_id:
            return
        products = await self._store.list_products()
        if not any(p.id == command.product_id for p in products):
            return
        if self.current_product_id and self.current_product_id != command.product_id:
            # The interrupted product was not finished: put it back in the queue.
            await self._store.set_product_status(self.current_product_id, "pending")
        self.current_product_id = command.product_id
        self._beat = 0
        await self._store.set_product_status(command.product_id, "presenting")
        await self._set_mode("presenting")

    async def _on_stop_products(self, _: Command) -> None:
        if self.current_product_id is not None:
            await self._store.set_product_status(self.current_product_id, "pending")
        self.current_product_id = None
        self._beat = 0
        self._resume_mode = "chatting"
        if self.mode != "paused":
            await self._set_mode("chatting")
        else:
            await self._publish()

    async def _on_say(self, command: Command) -> None:
        text = prompts.clean_spoken_text(command.text or "")[:500]
        if text:
            self._say_queue.append(text)

    async def _on_end(self, _: Command) -> None:
        self._ended = True

    # -------------------------------------------------------------- steps

    async def _chat_step(self) -> None:
        comments = self._take_comments()
        cue = prompts.chat_cue(self._profile.language, self._topic_index, comments)
        outcome = await self._turn(cue)
        if outcome == "spoken":
            if not comments:
                self._topic_index += 1
            # A waiting comment cuts the pause short so viewers get a quick reply.
            await self._idle(self._rng(*self._pacing.chat_gap))
        else:
            self._requeue_comments(comments)

    async def _present_step(self) -> None:
        products = await self._store.list_products()
        await self._refresh_instructions(products)
        product = await self._current_product(products)
        if product is None:
            await self._set_mode("chatting")
            await self._turn(prompts.products_done_cue(self._profile.language))
            return
        comments = self._take_comments()
        cue = prompts.product_cue(
            self._profile.language,
            product.facts,
            beat=self._beat,
            index=products.index(product) + 1,
            total=len(products),
            comments=comments,
        )
        outcome = await self._turn(cue)
        if outcome != "spoken":
            self._requeue_comments(comments)
            return
        self._beat += 1
        if self._beat >= prompts.PRODUCT_BEATS:
            await self._store.set_product_status(product.id, "done")
            self.current_product_id = None
            self._beat = 0
            await self._publish()
        await self._idle(self._rng(*self._pacing.product_gap), wake_on_comment=False)

    async def _current_product(self, products: list[LiveProduct]) -> LiveProduct | None:
        """Resolve the product on air, advancing to the next pending one."""
        if self.current_product_id is not None:
            for product in products:
                if product.id == self.current_product_id:
                    return product
            # Deleted from the studio mid-presentation.
            self.current_product_id = None
            self._beat = 0
        for product in products:
            if product.status in ("pending", "presenting"):
                self.current_product_id = product.id
                self._beat = 0
                await self._store.set_product_status(product.id, "presenting")
                await self._publish()
                return product
        return None

    # ------------------------------------------------------------ helpers

    async def _turn(self, cue: str) -> SpeakOutcome:
        if self._interrupt.is_set():
            return "interrupted"
        result = await self._speaker.speak(cue, self._interrupt)
        if result.outcome == "spoken":
            self._failures = 0
            self._last_line = prompts.clean_spoken_text(result.text or "") or self._last_line
            await self._publish()
        elif result.outcome == "failed":
            backoff = self._pacing.failure_backoff
            delay = backoff[min(self._failures, len(backoff) - 1)] if backoff else 0.0
            self._failures += 1
            logger.warning("live turn failed; backing off %.0fs", delay)
            await self._idle(delay, wake_on_comment=False)
        return result.outcome

    async def _idle(self, seconds: float | None, *, wake_on_comment: bool = True) -> None:
        """Wait up to ``seconds`` (forever when None), returning early on a command."""
        if seconds is not None and seconds <= 0:
            return
        loop = asyncio.get_running_loop()
        deadline = None if seconds is None else loop.time() + seconds
        while not self._commands:
            if wake_on_comment and self._comments and self.mode != "paused":
                return
            self._wake.clear()
            remaining = None if deadline is None else deadline - loop.time()
            if remaining is not None and remaining <= 0:
                return
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=remaining)

    def _take_comments(self) -> list[ViewerComment]:
        taken: list[ViewerComment] = []
        while self._comments and len(taken) < _MAX_COMMENTS_PER_TURN:
            taken.append(self._comments.popleft())
        return taken

    def _requeue_comments(self, comments: list[ViewerComment]) -> None:
        for comment in reversed(comments):
            self._comments.appendleft(comment)

    async def _refresh_instructions(self, products: list[LiveProduct]) -> None:
        """Keep the catalog in the system prompt in step with studio edits."""
        signature = tuple((p.id, p.facts.name, p.facts.price) for p in products)
        if signature == self._catalog_signature:
            return
        self._catalog_signature = signature
        await self._speaker.set_instructions(
            prompts.build_system_prompt(self._profile, [p.facts for p in products])
        )

    async def _set_mode(self, mode: Mode) -> None:
        self.mode = mode
        await self._publish()

    async def _publish(self) -> None:
        try:
            await self._store.publish_state(
                mode=self.mode,
                current_product_id=self.current_product_id,
                last_line=self._last_line,
            )
        except Exception:
            logger.exception("live state publish failed")

    async def _heartbeat(self) -> None:
        """Refresh the state timestamp and stop if the studio ended the session."""
        while True:
            await asyncio.sleep(self._pacing.heartbeat)
            try:
                if not await self._store.is_live():
                    logger.info("live session no longer live; stopping director")
                    self.submit(Command(action="end"))
                    return
            except Exception:
                logger.exception("live heartbeat check failed")
                continue
            await self._publish()
