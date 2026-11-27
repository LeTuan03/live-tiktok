"""What the restream feed should be encoding as the broadcast moves (pure).

The feed follows the broadcast the way the web player does: the on-air
position is derived from the stored playhead (:mod:`soshop.broadcast.timeline`)
and compared with what the feed is actually sending. :func:`decide` turns
that comparison into one :class:`Step`:

- nothing, while the feed is within :data:`MAX_LAG_MS` behind or
  :data:`AHEAD_MS` ahead of the derived position, which covers a feed still
  sending the last second of the previous slot;
- *queue* the next slot as soon as the current one has finished encoding, so
  it is ready to splice on without a gap;
- *restart* at the derived position after a control action (next, jump,
  pause, resume) or a drift beyond the allowance;
- *stop* once the show is off air.

Times are epoch milliseconds on the server clock. No I/O here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from soshop.broadcast.control import ON_AIR
from soshop.broadcast.timeline import Position, Timeline, following
from soshop.promo.assemble import price_label
from soshop.restream.card import ProductCard
from soshop.services.broadcasts import Broadcast, BroadcastItem, build_timeline

MAX_LAG_MS = 2000
"""How far the feed may trail the derived position before it re-cues."""
AHEAD_MS = 1500
"""How far ahead it may run (the relay sends a little early on purpose)."""
TAIL_MS = 300
"""A slot with less than this left is not worth a new encoder."""
STARTUP_GRACE_MS = 5000
"""An encoder that has sent nothing yet is given this long before it is judged."""
RETRY_MS = 2000
"""Wait this long before cueing a slot again after its encoder failed."""
_SAME_FRAME_MS = 100

CueKind = Literal["play", "hold"]
Slot = tuple[str, str, int, int]
"""``(phase, item_id, index, cycle)``: one slot of the show."""


def slot_of(position: Position) -> Slot:
    return (position.phase, position.item_id, position.index, position.cycle)


@dataclass(frozen=True)
class Source:
    """A playable item's video."""

    url: str
    video_ms: int
    title: str = ""
    card: ProductCard | None = None
    """What to show about the product it sells, ``None`` for intro, outro and no product."""


@dataclass(frozen=True)
class Cue:
    """One stretch for an encoder: a slot from ``position.offset_ms`` to its end."""

    kind: CueKind
    position: Position
    source: Source

    @property
    def slot(self) -> Slot:
        return slot_of(self.position)

    @property
    def start_ms(self) -> int:
        return self.position.offset_ms

    @property
    def remaining_ms(self) -> int:
        return self.position.remaining_ms

    @property
    def seek_ms(self) -> int:
        """Where in the video the stretch starts (a long slot repeats its video)."""
        return self.start_ms % max(self.source.video_ms, 1)

    @property
    def loops(self) -> bool:
        """True when the stretch runs past the end of the video and must wrap."""
        return self.seek_ms + self.remaining_ms > self.source.video_ms + 50


@dataclass(frozen=True)
class Show:
    """The broadcast as the feed needs it, at ``at_ms``."""

    timeline: Timeline
    position: Position
    playing: bool
    sources: Mapping[str, Source]
    at_ms: int

    def cue(self, position: Position, kind: CueKind) -> Cue | None:
        source = self.sources.get(position.item_id)
        return Cue(kind, position, source) if source is not None else None


def product_cards(items: list[BroadcastItem]) -> dict[str, ProductCard]:
    """The card of every product video, numbered by its place in the playlist.

    The number counts product videos in playlist order, skipped ones
    included, so it stays put when one video cannot play for a while.
    """
    cards: dict[str, ProductCard] = {}
    for number, item in enumerate(items, start=1):
        if item.product is None:
            continue
        product = item.product
        cards[item.id] = ProductCard(
            name=product.name,
            price=price_label(product.price, product.currency),
            number=number,
            image_url=product.image_url,
        )
    return cards


def show_of(broadcast: Broadcast) -> Show | None:
    """What is on air in ``broadcast``, or ``None`` when it is not on air."""
    playback = broadcast.playback
    where, head = playback.position, playback.playhead
    if playback.status not in ON_AIR or where is None or head is None:
        return None
    cards = product_cards(broadcast.items)
    items = [broadcast.intro, *broadcast.items, broadcast.outro]
    sources = {
        item.id: Source(
            item.video_url, max(round(item.video_s * 1000), 1), item.title, cards.get(item.id)
        )
        for item in items
        if item is not None and item.playable and item.video_url and item.video_s
    }
    timeline = build_timeline(
        broadcast.intro, broadcast.items, broadcast.outro, broadcast.loop_mode, broadcast.loop_count
    )
    position = Position(
        phase=where.phase,
        item_id=where.item_id,
        index=where.index,
        cycle=where.cycle,
        offset_ms=where.offset_ms,
        length_ms=where.length_ms,
    )
    return Show(timeline, position, head.playing, sources, playback.server_time_ms)


@dataclass(frozen=True)
class Feed:
    """What the relay is sending right now."""

    cue: Cue
    offset_ms: int
    """How far into the slot the feed has sent (``cue.start_ms`` before anything)."""
    started_ms: int
    sending: bool
    """At least one packet of this stretch went out."""
    encoded: bool
    """Its encoder finished: the whole stretch is buffered or sent."""
    queued: Cue | None = None


@dataclass(frozen=True)
class Failure:
    """The last stretch whose encoder failed, and when."""

    slot: Slot
    at_ms: int


@dataclass(frozen=True)
class Step:
    stop: bool = False
    restart: Cue | None = None
    """Drop whatever the feed holds and encode this now."""
    queue: Cue | None = None
    """Encode this next, to follow the current stretch without a gap."""


_NOTHING = Step()


def _worth(cue: Cue, failure: Failure | None, now_ms: int) -> Step:
    """Restart with ``cue``, unless that is pointless or too soon after a failure."""
    if cue.kind == "play" and cue.remaining_ms <= TAIL_MS:
        return _NOTHING
    if failure is not None and failure.slot == cue.slot and now_ms - failure.at_ms < RETRY_MS:
        return _NOTHING
    return Step(restart=cue)


def _lag(feed: Feed, show: Show) -> int | None:
    """How far the feed trails the show (negative: ahead), ``None`` if unrelated."""
    here = show.position
    if feed.cue.slot == slot_of(here):
        return here.offset_ms - feed.offset_ms
    after = following(show.timeline, feed.cue.position)
    if after is not None and slot_of(after) == slot_of(here):
        return (feed.cue.position.length_ms - feed.offset_ms) + here.offset_ms
    return None


def _hold(feed: Feed | None, cue: Cue, failure: Failure | None, now_ms: int) -> Step:
    if (
        feed is not None
        and feed.cue.kind == "hold"
        and feed.cue.slot == cue.slot
        and abs(feed.cue.start_ms - cue.start_ms) <= _SAME_FRAME_MS
    ):
        return _NOTHING
    return _worth(cue, failure, now_ms)


def _next(feed: Feed, show: Show) -> Step:
    """Queue the slot after the current stretch once its encoder is done."""
    if not feed.encoded or feed.queued is not None:
        return _NOTHING
    after = following(show.timeline, feed.cue.position)
    cue = show.cue(after, "play") if after is not None else None
    return Step(queue=cue) if cue is not None else _NOTHING


def decide(feed: Feed | None, show: Show | None, failure: Failure | None = None) -> Step:
    """The one thing to do now so the feed shows what ``show`` has on air."""
    if show is None:
        return Step(stop=True)
    now_ms = show.at_ms
    here = show.cue(show.position, "play" if show.playing else "hold")
    if here is None:
        return Step(stop=True)
    if here.kind == "hold":
        return _hold(feed, here, failure, now_ms)
    if feed is None or feed.cue.kind == "hold":
        return _worth(here, failure, now_ms)
    lag = _lag(feed, show)
    if lag is None:  # the show is somewhere else entirely: a control action
        return _worth(here, failure, now_ms)
    if lag > MAX_LAG_MS or lag < -AHEAD_MS:
        if not feed.sending and now_ms - feed.started_ms < STARTUP_GRACE_MS:
            return _NOTHING  # still starting up; judge it once it sends
        return _worth(here, failure, now_ms)
    return _next(feed, show)
