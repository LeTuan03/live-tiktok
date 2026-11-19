"""Keyless avatar fallback: the agent publishes the persona image as video.

Used when ``LEMONSLICE_API_KEY`` is not configured. The consumer feed only
renders a cell once it has a remote video track, so an audio-only agent shows
nothing. This publisher gives the feed a 9:16 track built from the persona's
still image, with a slow breathing zoom at rest and a livelier bob while the
agent is speaking. It is not lip sync. It is a stand-in that keeps the whole
loop (voice, rotation, product cards, swipes) runnable without LemonSlice.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import math
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiohttp
from PIL import Image, ImageDraw

if TYPE_CHECKING:
    from livekit import rtc

logger = logging.getLogger("soshop.agent.still_avatar")

WIDTH = 540
HEIGHT = 960
FPS = 12
# The base image is kept this much larger than the output so the zoom can
# crop into it without upscaling blur.
_OVERSCAN = 1.08
_FETCH_TIMEOUT_S = 15.0


def _cover(image: Image.Image, width: int, height: int) -> Image.Image:
    """Scale and center-crop ``image`` to fill ``width`` x ``height``."""
    scale = max(width / image.width, height / image.height)
    resized = image.resize((round(image.width * scale), round(image.height * scale)))
    left = (resized.width - width) // 2
    top = (resized.height - height) // 4  # bias upward, faces sit high in portraits
    return resized.crop((left, top, left + width, top + height))


def _placeholder(width: int, height: int) -> Image.Image:
    """Vertical gradient used when the persona image cannot be loaded."""
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        t = y / height
        draw.line(
            [(0, y), (width, y)],
            fill=(int(40 + 60 * t), int(20 + 30 * t), int(90 + 80 * (1 - t))),
        )
    return image


async def _read_image(source: str) -> bytes:
    """Read image bytes from an http(s) URL or a local file path."""
    if not source.startswith(("http://", "https://")):
        return await asyncio.to_thread(Path(source).read_bytes)
    timeout = aiohttp.ClientTimeout(total=_FETCH_TIMEOUT_S)
    async with aiohttp.ClientSession(timeout=timeout) as http, http.get(source) as resp:
        resp.raise_for_status()
        return await resp.read()


async def _load_base(image_url: str | None) -> Image.Image:
    size = (round(WIDTH * _OVERSCAN), round(HEIGHT * _OVERSCAN))
    if image_url:
        try:
            data = await _read_image(image_url)
            image = await asyncio.to_thread(
                lambda: _cover(Image.open(io.BytesIO(data)).convert("RGB"), *size)
            )
            return image
        except Exception:
            logger.exception("still avatar: image load failed, using placeholder")
    return _placeholder(*size)


def _render(base: Image.Image, zoom: float, dy: float) -> bytes:
    """Crop ``base`` at ``zoom`` (1.0 = widest) and return RGBA bytes."""
    crop_w = base.width / (_OVERSCAN * zoom) * (_OVERSCAN - 0.01)
    crop_h = base.height / (_OVERSCAN * zoom) * (_OVERSCAN - 0.01)
    left = (base.width - crop_w) / 2
    top = (base.height - crop_h) / 2 + dy
    top = min(max(top, 0.0), base.height - crop_h)
    frame = base.resize(
        (WIDTH, HEIGHT),
        resample=Image.Resampling.BILINEAR,
        box=(left, top, left + crop_w, top + crop_h),
    )
    return frame.convert("RGBA").tobytes()


class StillAvatar:
    """Publishes a gently animated still image on the agent's participant."""

    def __init__(self, room: rtc.Room) -> None:
        self._room = room
        self._base: Image.Image | None = None
        self._speaking = False
        self._task: asyncio.Task[None] | None = None
        self._source: Any = None

    async def start(self, session: Any, *, image_url: str | None) -> None:
        from livekit import rtc

        self._base = await _load_base(image_url)
        self._source = rtc.VideoSource(WIDTH, HEIGHT)
        track = rtc.LocalVideoTrack.create_video_track("avatar", self._source)
        await self._room.local_participant.publish_track(
            track,
            rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA, simulcast=False),
        )

        def _on_state(event: Any) -> None:
            self._speaking = getattr(event, "new_state", None) == "speaking"

        session.on("agent_state_changed", _on_state)
        self._task = asyncio.create_task(self._run(), name="still-avatar-frames")
        logger.info("still avatar started", extra={"has_image": bool(image_url)})

    async def update_image(self, image_url: str | None) -> None:
        """Swap the source image in place (persona swap)."""
        self._base = await _load_base(image_url)

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        from livekit import rtc

        started = time.monotonic()
        energy = 0.0  # eased 0..1 "speaking" level so motion ramps, never snaps
        interval = 1 / FPS
        while True:
            tick = time.monotonic()
            t = tick - started
            energy += ((1.0 if self._speaking else 0.0) - energy) * 0.25
            breathe = 0.012 * (0.5 + 0.5 * math.sin(t * 2 * math.pi / 5.0))
            talk = energy * 0.012 * (0.5 + 0.5 * math.sin(t * 2 * math.pi * 1.6))
            dy = energy * 4.0 * math.sin(t * 2 * math.pi * 0.8)
            base = self._base
            if base is not None:
                try:
                    data = await asyncio.to_thread(_render, base, 1.0 + breathe + talk, dy)
                    self._source.capture_frame(
                        rtc.VideoFrame(WIDTH, HEIGHT, rtc.VideoBufferType.RGBA, data)
                    )
                except Exception:
                    logger.exception("still avatar: frame render failed")
            await asyncio.sleep(max(0.0, interval - (time.monotonic() - tick)))
