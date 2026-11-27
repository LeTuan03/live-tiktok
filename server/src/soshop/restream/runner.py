"""Keep every on-air broadcast pushed to the outputs switched on for it.

:class:`Restreamer` runs inside the API process, started from the lifespan when
``RESTREAM_ENABLED`` is on. Every :data:`PASS_S` it reads which outputs should
be pushing (switched on, on a broadcast that is live or paused), keeps one
:class:`~soshop.restream.relay.Relay` per such broadcast, applies
:func:`~soshop.restream.plan.decide` to it, and writes each output's state to
``broadcast_outputs`` when it changes, for the control room to show.

A restart loses only a few seconds of stream: the next process reads the same
rows and picks the show up at its derived position. Run it in one API process
only, since two would push to the same stream keys.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import uuid4

import asyncpg

from soshop.promo.assemble import probe
from soshop.restream import plan
from soshop.restream.encoder import Profile
from soshop.restream.platforms import OutputStatus
from soshop.restream.relay import Output, Relay
from soshop.services import broadcasts
from soshop.services import restream as store
from soshop_server import storage
from soshop_server.core.config import Settings, settings
from soshop_server.db.pool import get_pool

logger = logging.getLogger(__name__)

PASS_S = 0.5
_MEDIA_CACHE = 256

Key = tuple[str, str]
"""``(broadcast_id, destination_id)``."""


@dataclass(frozen=True)
class _Media:
    path: str
    has_audio: bool


def _resolve(url: str) -> _Media | None:
    """A local file for a video URL from the media store, downloaded if needed."""
    key = storage.get_store().key_for_url(url)
    path = storage.local_file(key) if key else None
    if path is None or not path.is_file():
        return None
    duration, has_audio = probe(path)
    return _Media(str(path), has_audio) if duration > 0 else None


class Restreamer:
    def __init__(self, config: Settings) -> None:
        self._profile = Profile.from_settings(config)
        self._ffmpeg = shutil.which("ffmpeg")
        self._relays: dict[str, Relay] = {}
        self._written: dict[Key, OutputStatus] = {}
        self._media: dict[str, _Media] = {}
        self._closing: set[asyncio.Task[None]] = set()
        self.problem = None if self._ffmpeg else "Chưa cài ffmpeg trên server (cần có trong PATH)."

    async def run(self) -> None:
        if self._ffmpeg is None:
            logger.warning("restream disabled: ffmpeg is not on PATH")
            return
        try:
            # Scratch space (paused frames) left by a previous process.
            shutil.rmtree(storage.work_dir("restream"), ignore_errors=True)
            await self._with_conn(store.reset_states)
            while True:
                try:
                    await self._pass()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("restream pass failed; retrying")
                await asyncio.sleep(PASS_S)
        finally:
            await self._shutdown()

    # ------------------------------------------------------------------ pass

    async def _with_conn(self, fn: Callable[[asyncpg.Connection], Awaitable[None]]) -> None:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await fn(conn)

    async def _pass(self) -> None:
        pool = await get_pool()
        async with pool.acquire() as conn:
            groups: dict[str, list[store.Wanted]] = defaultdict(list)
            for wanted in await store.wanted_outputs(conn):
                groups[wanted.broadcast_id].append(wanted)
            for bid in [b for b in self._relays if b not in groups]:
                self._drop(bid)
            states: dict[Key, OutputStatus] = {}
            for bid, outputs in groups.items():
                states.update(await self._follow(conn, bid, outputs))
            await self._write(conn, states)

    async def _follow(
        self, conn: asyncpg.Connection, bid: str, outputs: list[store.Wanted]
    ) -> dict[Key, OutputStatus]:
        """Bring one broadcast's relay in line with the show; its outputs' states."""
        states = {
            (bid, w.destination_id): OutputStatus("error", w.problem or "")
            for w in outputs
            if w.url is None
        }
        usable = {w.destination_id: Output(w.url, w.secrets) for w in outputs if w.url}
        broadcast = await broadcasts.get_broadcast(conn, bid)
        show = plan.show_of(broadcast) if broadcast is not None else None
        if not usable or show is None:
            self._drop(bid)
            return states
        relay = self._relays.get(bid)
        if relay is None:
            relay = self._relays[bid] = self._new_relay(bid)
        await asyncio.to_thread(relay.set_outputs, usable)
        step = plan.decide(relay.feed(), show, relay.failure())
        if step.stop:
            self._drop(bid)
            return states
        await self._apply(relay, step)
        await asyncio.to_thread(relay.tick)
        problem = relay.problem()
        for did, status in relay.statuses().items():
            if problem and status.state == "connecting":
                status = OutputStatus("retrying", problem)
            states[(bid, did)] = status
        return states

    async def _apply(self, relay: Relay, step: plan.Step) -> None:
        cue = step.restart or step.queue
        if cue is None:
            return
        media = await self._media_for(cue.source.url)
        if media is None:
            relay.note_failure(cue, f'Không tìm thấy tệp video "{cue.source.title}".')
            return
        action = relay.start if step.restart is not None else relay.queue
        await asyncio.to_thread(action, cue, media.path, media.has_audio)

    async def _media_for(self, url: str) -> _Media | None:
        found = self._media.get(url)
        if found is not None:
            return found
        try:
            found = await asyncio.to_thread(_resolve, url)
        except Exception:
            logger.exception("restream could not read %s", url)
            return None
        if found is not None:
            if len(self._media) >= _MEDIA_CACHE:
                self._media.clear()
            self._media[url] = found  # media keys never change content
        return found

    def _new_relay(self, bid: str) -> Relay:
        assert self._ffmpeg is not None  # noqa: S101 - run() returns early without it
        work = storage.work_dir("restream", f"{bid}-{uuid4().hex[:8]}")
        return Relay(self._ffmpeg, self._profile, work)

    def _drop(self, bid: str) -> None:
        """Stop pushing ``bid`` in the background (a live stream ends cleanly first)."""
        relay = self._relays.pop(bid, None)
        if relay is None:
            return
        task = asyncio.create_task(asyncio.to_thread(_close, relay))
        self._closing.add(task)
        task.add_done_callback(self._closing.discard)

    async def _write(self, conn: asyncpg.Connection, states: dict[Key, OutputStatus]) -> None:
        for key, old in self._written.items():
            if key not in states and old.state != "off":
                states[key] = OutputStatus("off")
        for (bid, did), status in states.items():
            if self._written.get((bid, did)) != status:
                await store.save_status(conn, bid, did, status)
                self._written[(bid, did)] = status

    async def _shutdown(self) -> None:
        for bid in list(self._relays):
            self._drop(bid)
        await asyncio.gather(*self._closing, return_exceptions=True)
        with contextlib.suppress(Exception):
            await self._with_conn(store.reset_states)


def _close(relay: Relay) -> None:
    relay.close()
    shutil.rmtree(relay.work, ignore_errors=True)


# ---------------------------------------------------------------- lifecycle

_current: Restreamer | None = None


def start() -> asyncio.Task[None] | None:
    """Start the restreamer in this process if ``RESTREAM_ENABLED``; its task."""
    global _current
    if not settings.RESTREAM_ENABLED:
        return None
    _current = Restreamer(settings)
    return asyncio.create_task(_current.run(), name="restreamer")


def availability() -> tuple[bool, str | None]:
    """Whether this API process pushes streams, and why not."""
    if not settings.RESTREAM_ENABLED:
        return False, "Đẩy luồng đang tắt trên server (RESTREAM_ENABLED=false)."
    if _current is None:
        return False, "Bộ đẩy luồng chưa chạy trên server này."
    if _current.problem:
        return False, _current.problem
    return True, None
