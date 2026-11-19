"""Feed ranking service.

Picks the next N `(persona, product?)` pairs to surface in the cursor feed.

The route layer is responsible for fetching eligible *candidates* (active
personas, scripts whose product belongs to a non-paused campaign, plus
freestyle/comedy scripts) and the user's recent persona history. This
module only ranks: it consumes a list of `FeedCandidate` and returns a
list of `FeedItem` plus an opaque pagination cursor.

The `FeedRankingService` Protocol is the seam an ML ranker can drop into
later: the route never references `WeightedRandomFeedRanker` directly,
only the Protocol, so swapping implementations is a one-line change.

V1 ranking rules
----------------
- **Cooldown**: persona ids in `recent_personas` are never selected.
- **Comedy slots**: with probability `comedy_ratio` (default 0.25), the
  ranker prefers a freestyle (`is_comedy=True`) candidate for that slot,
  to break up ad fatigue. Falls back to ad candidates if no comedy
  options are eligible (and vice versa).
- **Weight bias**: within the chosen pool, candidates are picked with
  `random.choices(weights=...)` over their `weight` field, so higher-
  weighted `persona_scripts` rows are favored.
- **Stable pagination**: the cursor encodes the rng seed and the persona
  ids already emitted in this cursor chain. A second `rank()` call with
  the same cursor yields a deterministic continuation that does not
  repeat personas from earlier pages.
"""

from __future__ import annotations

import base64
import json
import random
import secrets
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

__all__ = [
    "FeedCandidate",
    "FeedCursor",
    "FeedItem",
    "FeedRankingResult",
    "FeedRankingService",
    "WeightedRandomFeedRanker",
    "decode_cursor",
    "encode_cursor",
]


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FeedCandidate:
    """A single (persona, script, product?) tuple eligible for selection."""

    persona_id: UUID
    script_id: UUID
    product_id: UUID | None
    weight: int = 1

    @property
    def is_ad(self) -> bool:
        return self.product_id is not None

    @property
    def is_comedy(self) -> bool:
        return self.product_id is None


@dataclass(frozen=True)
class FeedItem:
    """An entry in the ranked feed page."""

    persona_id: UUID
    script_id: UUID
    product_id: UUID | None
    is_ad: bool


@dataclass(frozen=True)
class FeedCursor:
    """Opaque pagination state.

    `seed` keeps the random stream stable across pages. `shown` is the
    set of persona ids already emitted in this cursor chain — we exclude
    them on subsequent pages so the same persona never repeats within a
    cursor session.
    """

    seed: int
    shown: list[UUID] = field(default_factory=list)


@dataclass(frozen=True)
class FeedRankingResult:
    items: list[FeedItem]
    next_cursor: FeedCursor


# ---------------------------------------------------------------------------
# Cursor codec — opaque to clients (base64'd JSON)
# ---------------------------------------------------------------------------


def encode_cursor(cursor: FeedCursor) -> str:
    payload = {
        "s": cursor.seed,
        "x": [str(pid) for pid in cursor.shown],
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(token: str | None) -> FeedCursor | None:
    if not token:
        return None
    try:
        padding = "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(token + padding)
        payload = json.loads(raw)
        seed = int(payload["s"])
        shown = [UUID(x) for x in payload.get("x", [])]
    except (ValueError, KeyError, TypeError):
        return None
    return FeedCursor(seed=seed, shown=shown)


# ---------------------------------------------------------------------------
# Ranking service interface
# ---------------------------------------------------------------------------


class FeedRankingService(Protocol):
    """Ranks eligible candidates into a paginated feed page.

    An ML implementation would substitute a learned scoring function for
    the weighted-random draw below while keeping this signature.
    """

    def rank(
        self,
        candidates: list[FeedCandidate],
        *,
        recent_personas: list[UUID],
        limit: int,
        cursor: FeedCursor | None = None,
    ) -> FeedRankingResult: ...


# ---------------------------------------------------------------------------
# V1 ranker
# ---------------------------------------------------------------------------


class WeightedRandomFeedRanker:
    """Weighted-random ranker with comedy-slot blending and persona cooldown."""

    def __init__(
        self,
        *,
        comedy_ratio: float = 0.25,
        seed: int | None = None,
    ) -> None:
        if not 0.0 <= comedy_ratio <= 1.0:
            raise ValueError("comedy_ratio must be in [0, 1]")
        self._comedy_ratio = comedy_ratio
        # `seed` is used only for tests / deterministic reproductions; the
        # cursor's seed wins at runtime.
        self._fixed_seed = seed

    def rank(
        self,
        candidates: list[FeedCandidate],
        *,
        recent_personas: list[UUID],
        limit: int,
        cursor: FeedCursor | None = None,
    ) -> FeedRankingResult:
        if limit <= 0:
            return FeedRankingResult(items=[], next_cursor=cursor or self._new_cursor())

        if cursor is None:
            cursor = self._new_cursor()

        rng = random.Random(cursor.seed)  # noqa: S311 — non-cryptographic ranking
        # Advance the rng past previously-emitted picks so subsequent pages
        # consume *new* random draws even though we re-seed from the same
        # value. One draw per already-shown item is a good-enough proxy.
        for _ in cursor.shown:
            rng.random()

        cooldown: set[UUID] = set(recent_personas) | set(cursor.shown)
        # Persona-level dedupe within this single page: even if multiple
        # scripts exist for the same persona, surface each persona at most
        # once per page so the user sees variety.
        page_persona_set: set[UUID] = set()

        items: list[FeedItem] = []
        new_shown: list[UUID] = list(cursor.shown)
        pool: list[FeedCandidate] = list(candidates)

        while len(items) < limit:
            blocked = cooldown | page_persona_set
            eligible = [c for c in pool if c.persona_id not in blocked]
            if not eligible:
                break

            want_comedy = rng.random() < self._comedy_ratio
            preferred = [c for c in eligible if c.is_comedy == want_comedy]
            choice_pool = preferred if preferred else eligible

            weights = [max(c.weight, 1) for c in choice_pool]
            picked = rng.choices(choice_pool, weights=weights, k=1)[0]
            pool.remove(picked)

            items.append(
                FeedItem(
                    persona_id=picked.persona_id,
                    script_id=picked.script_id,
                    product_id=picked.product_id,
                    is_ad=picked.is_ad,
                )
            )
            page_persona_set.add(picked.persona_id)
            new_shown.append(picked.persona_id)

        return FeedRankingResult(
            items=items,
            next_cursor=FeedCursor(seed=cursor.seed, shown=new_shown),
        )

    def _new_cursor(self) -> FeedCursor:
        seed = self._fixed_seed if self._fixed_seed is not None else secrets.randbits(63)
        return FeedCursor(seed=seed, shown=[])
