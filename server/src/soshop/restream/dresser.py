"""What each restream stretch wears on top of its video: card, loudness gain, music.

:class:`Dresser` turns the settings and the product on air into an
:class:`~soshop.restream.encoder.Dressing` for the feeder. Everything it works
out is cached for the life of the process: a card is drawn once per product
(``RESTREAM_OVERLAY``), a file's loudness is measured once
(``RESTREAM_NORMALIZE_AUDIO``), the music bed is checked once at startup
(``RESTREAM_MUSIC_FILE``). Blocking; the restreamer calls it from a thread.
A problem with any of them leaves that part out and never stops the stream.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
from pathlib import Path

from soshop.promo.assemble import probe
from soshop.restream import card as cards
from soshop.restream.card import ProductCard
from soshop.restream.encoder import (
    Dressing,
    Music,
    Overlay,
    Profile,
    gain_for,
    loudness_args,
    parse_loudness,
)
from soshop_server import storage
from soshop_server.core.config import Settings

logger = logging.getLogger(__name__)

_CACHE = 256
_MEASURE_TIMEOUT_S = 120
_BED_LUFS = -14.0
"""Loudness the music bed is brought to before its volume, when clips are not normalised."""


def _local(url: str | None) -> Path | None:
    """A file on this machine for a media URL (a product photo), if there is one."""
    if not url:
        return None
    key = storage.get_store().key_for_url(url)
    try:
        path = storage.local_file(key) if key else None
    except Exception:
        logger.warning("restream could not fetch %s", url, exc_info=True)
        return None
    return path if path is not None and path.is_file() else None


class Dresser:
    def __init__(self, config: Settings, ffmpeg: str, profile: Profile) -> None:
        self._ffmpeg = ffmpeg
        self._profile = profile
        self._placement: cards.Placement | None = (
            None if config.RESTREAM_OVERLAY == "off" else config.RESTREAM_OVERLAY
        )
        self._target = config.RESTREAM_AUDIO_LUFS if config.RESTREAM_NORMALIZE_AUDIO else None
        self._music_file = config.RESTREAM_MUSIC_FILE.strip()
        self._music_volume = config.RESTREAM_MUSIC_VOLUME
        self.music: Music | None = None
        self._overlays: dict[ProductCard, Overlay | None] = {}
        self._gains: dict[str, float] = {}

    def prepare(self) -> None:
        """Check the music bed once, before the first stretch."""
        if not self._music_file or self._music_volume <= 0:
            return
        path = Path(self._music_file)
        length, has_audio = probe(path) if path.is_file() else (0.0, False)
        if length <= 0 or not has_audio:
            logger.warning("RESTREAM_MUSIC_FILE %s is not a playable audio file; no music", path)
            return
        # Bring the track to the clips' loudness first, so the volume setting
        # means the same thing whatever file is used (masters vary by 20 dB).
        level = self._measure(str(path))
        gain = gain_for(level, self._target if self._target is not None else _BED_LUFS)
        volume = min(self._music_volume * 10 ** (gain / 20), 1.0)
        self.music = Music(str(path), length, round(volume, 4))
        logger.info("restream music bed: %s, %s LUFS, volume %.3f", path.name, level, volume)

    def dress(self, card: ProductCard | None, source: str, has_audio: bool) -> Dressing:
        gain = self.gain(source) if has_audio else 0.0
        return Dressing(overlay=self.overlay(card), gain_db=gain, music=self.music)

    def gain(self, source: str) -> float:
        """dB that bring ``source`` to the target loudness (0 when off or unknown)."""
        if self._target is None:
            return 0.0
        found = self._gains.get(source)
        if found is not None:
            return found
        measured = self._measure(source)
        gain = gain_for(measured, self._target)
        if len(self._gains) >= _CACHE:
            self._gains.clear()
        self._gains[source] = gain  # media keys never change content
        logger.info("restream loudness %s: %s LUFS, gain %+.1f dB", source, measured, gain)
        return gain

    def _measure(self, source: str) -> float | None:
        """Integrated loudness of ``source`` in LUFS, ``None`` if it cannot be read."""
        try:
            done = subprocess.run(  # noqa: S603 - fixed argv, our own ffmpeg
                loudness_args(self._ffmpeg, source),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_MEASURE_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            logger.warning("restream could not measure loudness of %s", source, exc_info=True)
            return None
        return parse_loudness(done.stderr)

    def overlay(self, card: ProductCard | None) -> Overlay | None:
        """The product card as a PNG over the picture, drawn once per product."""
        if card is None or self._placement is None:
            return None
        if card in self._overlays:
            return self._overlays[card]
        made: Overlay | None = None
        size = (self._profile.width, self._profile.height)
        digest = hashlib.sha1(  # noqa: S324 - a file name, not security
            repr((card, size, self._placement)).encode()
        ).hexdigest()[:16]
        out = storage.work_dir("restream", "cards") / f"{digest}.png"
        try:
            x, y = cards.write(
                card,
                _local(card.image_url),
                out,
                frame_w=size[0],
                frame_h=size[1],
                placement=self._placement,
            )
            made = Overlay(str(out), x, y)
        except (cards.CardError, OSError, ValueError):
            logger.warning("restream could not draw the card for %s", card.name, exc_info=True)
        if len(self._overlays) >= _CACHE:
            self._overlays.clear()
        self._overlays[card] = made
        return made
