"""Unit tests for the feed ranking service.

Covers the four behaviors called out in the acceptance criteria:
cooldown, paused-campaign exclusion, weight bias, comedy-slot ratio.

The paused-campaign rule is enforced at the SQL layer (in `feed.py`),
not in the ranker — we test it here against the candidate-loading SQL
using an in-process fake `Connection` that mimics asyncpg's API.
"""

from __future__ import annotations

from collections import Counter
from uuid import UUID, uuid4

import pytest

from soshop.services.feed_ranking import (
    FeedCandidate,
    FeedCursor,
    WeightedRandomFeedRanker,
    decode_cursor,
    encode_cursor,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _candidate(
    *,
    persona_id: UUID | None = None,
    weight: int = 1,
    is_comedy: bool = False,
) -> FeedCandidate:
    return FeedCandidate(
        persona_id=persona_id or uuid4(),
        script_id=uuid4(),
        product_id=None if is_comedy else uuid4(),
        weight=weight,
    )


# ---------------------------------------------------------------------------
# Cooldown
# ---------------------------------------------------------------------------


def test_cooldown_excludes_recent_personas() -> None:
    blocked = uuid4()
    candidates = [
        _candidate(persona_id=blocked, weight=1000),  # huge weight, must still be skipped
        _candidate(weight=1),
        _candidate(weight=1),
    ]
    ranker = WeightedRandomFeedRanker(seed=42, comedy_ratio=0.0)
    result = ranker.rank(candidates, recent_personas=[blocked], limit=3)

    chosen = {item.persona_id for item in result.items}
    assert blocked not in chosen
    assert len(result.items) == 2  # only two non-blocked personas existed


def test_cooldown_persists_across_pages_via_cursor() -> None:
    """Personas emitted on page N must not reappear on page N+1."""
    candidates = [_candidate(weight=1) for _ in range(10)]
    ranker = WeightedRandomFeedRanker(seed=1, comedy_ratio=0.0)

    page1 = ranker.rank(candidates, recent_personas=[], limit=4)
    page2 = ranker.rank(
        candidates,
        recent_personas=[],
        limit=4,
        cursor=page1.next_cursor,
    )

    page1_ids = {item.persona_id for item in page1.items}
    page2_ids = {item.persona_id for item in page2.items}
    assert page1_ids.isdisjoint(page2_ids)
    assert len(page1.items) == 4
    assert len(page2.items) == 4


def test_no_persona_repeats_within_a_single_page() -> None:
    persona = uuid4()
    # Same persona, multiple scripts — only one should land on the page.
    candidates = [
        FeedCandidate(persona_id=persona, script_id=uuid4(), product_id=uuid4(), weight=1)
        for _ in range(5)
    ]
    candidates += [_candidate(weight=1) for _ in range(3)]

    ranker = WeightedRandomFeedRanker(seed=7, comedy_ratio=0.0)
    result = ranker.rank(candidates, recent_personas=[], limit=8)
    persona_ids = [item.persona_id for item in result.items]
    assert persona_ids.count(persona) <= 1


# ---------------------------------------------------------------------------
# Weight bias
# ---------------------------------------------------------------------------


def test_weight_bias_favors_higher_weighted_candidates() -> None:
    """Over many trials, a 100x-weighted candidate should dominate selection."""
    heavy = uuid4()
    light_a = uuid4()
    light_b = uuid4()

    counts: Counter[UUID] = Counter()
    trials = 400
    for trial in range(trials):
        candidates = [
            FeedCandidate(persona_id=heavy, script_id=uuid4(), product_id=uuid4(), weight=100),
            FeedCandidate(persona_id=light_a, script_id=uuid4(), product_id=uuid4(), weight=1),
            FeedCandidate(persona_id=light_b, script_id=uuid4(), product_id=uuid4(), weight=1),
        ]
        ranker = WeightedRandomFeedRanker(seed=trial, comedy_ratio=0.0)
        result = ranker.rank(candidates, recent_personas=[], limit=1)
        counts[result.items[0].persona_id] += 1

    assert counts[heavy] / trials > 0.85, f"heavy persona only won {counts[heavy]}/{trials}"


# ---------------------------------------------------------------------------
# Comedy-slot ratio
# ---------------------------------------------------------------------------


def test_comedy_slot_ratio_roughly_matches_target() -> None:
    """With comedy_ratio=0.25 and a balanced candidate pool the emitted
    feed should be ~25% comedy slots over a large sample."""
    trials = 600
    comedy_count = 0
    total = 0

    for trial in range(trials):
        # Fresh candidate pool per trial: 10 ad scripts + 10 comedy scripts
        # across 20 distinct personas, so neither pool ever runs dry.
        ads = [_candidate(weight=1, is_comedy=False) for _ in range(10)]
        comedies = [_candidate(weight=1, is_comedy=True) for _ in range(10)]
        ranker = WeightedRandomFeedRanker(seed=trial, comedy_ratio=0.25)
        result = ranker.rank(ads + comedies, recent_personas=[], limit=4)
        for item in result.items:
            total += 1
            if not item.is_ad:
                comedy_count += 1

    rate = comedy_count / total
    # 25% target with ±7pp tolerance — wide enough for sample noise but
    # tight enough to fail if the pick logic is broken.
    assert 0.18 <= rate <= 0.32, f"comedy rate {rate:.3f} outside expected band"


def test_comedy_ratio_zero_emits_no_comedy_when_ads_available() -> None:
    ads = [_candidate(weight=1, is_comedy=False) for _ in range(5)]
    comedies = [_candidate(weight=1, is_comedy=True) for _ in range(5)]
    ranker = WeightedRandomFeedRanker(seed=99, comedy_ratio=0.0)
    result = ranker.rank(ads + comedies, recent_personas=[], limit=5)
    assert all(item.is_ad for item in result.items)


def test_comedy_ratio_one_emits_only_comedy_when_available() -> None:
    ads = [_candidate(weight=1, is_comedy=False) for _ in range(5)]
    comedies = [_candidate(weight=1, is_comedy=True) for _ in range(5)]
    ranker = WeightedRandomFeedRanker(seed=99, comedy_ratio=1.0)
    result = ranker.rank(ads + comedies, recent_personas=[], limit=5)
    assert all(not item.is_ad for item in result.items)


def test_comedy_slot_falls_back_to_ads_when_no_comedy_available() -> None:
    ads = [_candidate(weight=1, is_comedy=False) for _ in range(5)]
    ranker = WeightedRandomFeedRanker(seed=1, comedy_ratio=1.0)
    result = ranker.rank(ads, recent_personas=[], limit=3)
    assert len(result.items) == 3
    assert all(item.is_ad for item in result.items)


# ---------------------------------------------------------------------------
# Cursor codec
# ---------------------------------------------------------------------------


def test_cursor_round_trip() -> None:
    cursor = FeedCursor(seed=12345, shown=[uuid4(), uuid4(), uuid4()])
    decoded = decode_cursor(encode_cursor(cursor))
    assert decoded == cursor


def test_decode_cursor_handles_garbage() -> None:
    assert decode_cursor("not-a-real-cursor") is None
    assert decode_cursor("") is None
    assert decode_cursor(None) is None


def test_invalid_comedy_ratio_rejected() -> None:
    with pytest.raises(ValueError):
        WeightedRandomFeedRanker(comedy_ratio=1.5)
    with pytest.raises(ValueError):
        WeightedRandomFeedRanker(comedy_ratio=-0.1)


# ---------------------------------------------------------------------------
# Paused-campaign exclusion (SQL-layer concern, exercised against fake conn)
# ---------------------------------------------------------------------------


class _FakeConn:
    """Minimal stand-in for asyncpg.Connection used by feed.py loaders.

    We don't run a real Postgres in unit tests; instead we capture the
    SQL string and assert its semantics include the paused-campaign
    exclusion. The actual SQL execution is covered by integration tests
    when those land.
    """

    def __init__(self, fetch_rows: list[dict] | None = None) -> None:
        self.fetch_rows = fetch_rows or []
        self.last_query: str | None = None
        self.last_args: tuple = ()

    async def fetch(self, query: str, *args: object) -> list[dict]:
        self.last_query = query
        self.last_args = args
        return self.fetch_rows

    async def fetchrow(self, query: str, *args: object) -> dict | None:
        self.last_query = query
        self.last_args = args
        return self.fetch_rows[0] if self.fetch_rows else None


@pytest.mark.asyncio
async def test_candidate_sql_excludes_paused_campaigns() -> None:
    """The candidates query must filter out scripts whose only campaigns
    are paused / archived / completed, AND must require every slot to
    advertise a product — every AI seller in v1 is product-bound."""
    from soshop.api.routes.feed import _CANDIDATES_SQL, _load_candidates

    fake = _FakeConn(fetch_rows=[])
    await _load_candidates(fake)  # type: ignore[arg-type]

    query = fake.last_query or ""
    assert query == _CANDIDATES_SQL
    assert "'paused'" in query
    assert "NOT IN" in query
    # Every slot must be product-bound: freestyle scripts (product_id
    # IS NULL) never surface in the feed.
    assert "ps.product_id IS NOT NULL" in query


@pytest.mark.asyncio
async def test_recent_personas_sql_scopes_by_user_and_limit() -> None:
    from soshop.api.routes.feed import _COOLDOWN_WINDOW, _load_recent_personas

    fake = _FakeConn(fetch_rows=[])
    await _load_recent_personas(fake, "user_abc", _COOLDOWN_WINDOW)  # type: ignore[arg-type]

    assert fake.last_args == ("user_abc", _COOLDOWN_WINDOW)
    assert _COOLDOWN_WINDOW == 8  # AC: last 8 swipes
    assert "user_id" in (fake.last_query or "")
