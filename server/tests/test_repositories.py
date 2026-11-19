"""Integration tests for the AdvertiserRepository + FeedRepository.

Per `server/CLAUDE.md` mocked DB tests are explicitly forbidden — past
mocked-DB tests masked migration breakage. These repositories own SQL,
so the only way to verify they line up with the live schema is to hit a
real Postgres. Skipped by default; runs when `DATABASE_URL` is set,
which is how CI exercises the same suite against Neon.

Pattern follows `tests/test_agent_loaders.py`: each test seeds the rows
it needs, runs assertions, and deletes in `finally`. We can't rely on
transaction rollback because Neon's pgbouncer (transaction-pool mode)
doesn't pin a connection across statements — the repo opens its own
connection separate from the seeder.

Two empty-list short-circuit paths in `FeedRepository` are pure
in-process logic (early `return {}` when the input list is empty) — those
are exercised at the bottom of the file without a DB so they run in
the hermetic default suite.
"""

from __future__ import annotations

import os
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from soshop.repositories.advertiser import AdvertiserRepository
from soshop.repositories.feed import FeedRepository, PersonaMetaRow

# ---------------------------------------------------------------------------
# DB-backed integration suite
# ---------------------------------------------------------------------------

_DB_REQUIRED = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set — repository integration tests skipped",
)


@pytest.fixture
async def pool():
    pool = await asyncpg.create_pool(
        os.environ["DATABASE_URL"],
        min_size=1,
        max_size=2,
        statement_cache_size=0,
    )
    try:
        yield pool
    finally:
        await pool.close()


async def _seed_advertiser(pool, *, org_id: str | None = None) -> UUID:
    org = org_id or f"org_test_{uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO advertisers (org_id, name) VALUES ($1, $2) RETURNING id",
            org,
            "Repo Test Co.",
        )


async def _seed_persona(pool, *, name: str = "Repo Persona") -> UUID:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO personas
                (name, avatar_image_url, voice_id, voice_pipeline_id,
                 personality, backstory, traits, persona_kind, status)
            VALUES ($1, $2, $3, $4, $5, $6, $7, 'host', 'active')
            RETURNING id
            """,
            name,
            "https://example/avatar.jpg",
            "voice-x",
            "elevenlabs-eleven_turbo_v2_5",
            "calm",
            "backstory",
            "traits",
        )


async def _seed_product_media(pool, product_id: UUID, position: int, kind: str, url: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO product_media (product_id, position, kind, url)
            VALUES ($1, $2, $3, $4)
            """,
            product_id,
            position,
            kind,
            url,
        )


async def _delete_advertiser_cascade(pool, advertiser_id: UUID) -> None:
    async with pool.acquire() as conn:
        # ON DELETE CASCADE on advertiser_id sweeps products + campaigns.
        await conn.execute("DELETE FROM advertisers WHERE id = $1", advertiser_id)


async def _delete_persona(pool, persona_id: UUID) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM personas WHERE id = $1", persona_id)


# ---------------------------------------------------------------------------
# AdvertiserRepository
# ---------------------------------------------------------------------------


@_DB_REQUIRED
async def test_get_by_org_id_returns_advertiser_when_present(pool):
    org_id = f"org_test_{uuid4().hex[:8]}"
    advertiser_id = await _seed_advertiser(pool, org_id=org_id)
    try:
        async with pool.acquire() as conn:
            repo = AdvertiserRepository(conn)
            adv = await repo.get_by_org_id(org_id)
            assert adv is not None
            assert adv.id == advertiser_id
            assert adv.org_id == org_id
    finally:
        await _delete_advertiser_cascade(pool, advertiser_id)


@_DB_REQUIRED
async def test_get_by_org_id_returns_none_for_unknown_org(pool):
    async with pool.acquire() as conn:
        repo = AdvertiserRepository(conn)
        assert await repo.get_by_org_id(f"org_missing_{uuid4().hex[:6]}") is None


@_DB_REQUIRED
async def test_insert_advertiser_round_trips(pool):
    org_id = f"org_test_{uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        repo = AdvertiserRepository(conn)
        adv = await repo.insert_advertiser(
            org_id=org_id,
            name="Brand New Co.",
            contact_email="ops@brand-new.test",
        )
        try:
            assert adv.org_id == org_id
            assert adv.name == "Brand New Co."
            assert adv.contact_email == "ops@brand-new.test"
            # And it's retrievable.
            loaded = await repo.get_by_org_id(org_id)
            assert loaded is not None
            assert loaded.id == adv.id
        finally:
            await _delete_advertiser_cascade(pool, adv.id)


@_DB_REQUIRED
async def test_insert_product_and_get_owned_isolates_advertisers(pool):
    """A product owned by advertiser A must not be returned for B."""
    a_id = await _seed_advertiser(pool)
    b_id = await _seed_advertiser(pool)
    try:
        async with pool.acquire() as conn:
            repo = AdvertiserRepository(conn)
            product = await repo.insert_product(
                advertiser_id=a_id,
                sku=f"SKU-{uuid4().hex[:6]}",
                title="A's Product",
                description=None,
                deep_link_url=None,
                landing_url=None,
                price_cents=999,
                currency="USD",
                status="draft",
            )
            # Owned by A → returned for A.
            owned_by_a = await repo.get_owned_product(a_id, product.id)
            assert owned_by_a is not None
            # NOT returned for B (even though product exists).
            owned_by_b = await repo.get_owned_product(b_id, product.id)
            assert owned_by_b is None
    finally:
        await _delete_advertiser_cascade(pool, a_id)
        await _delete_advertiser_cascade(pool, b_id)


@_DB_REQUIRED
async def test_list_products_respects_status_filter_and_pagination(pool):
    advertiser_id = await _seed_advertiser(pool)
    try:
        async with pool.acquire() as conn:
            repo = AdvertiserRepository(conn)
            for i in range(3):
                await repo.insert_product(
                    advertiser_id=advertiser_id,
                    sku=f"DRAFT-{i}-{uuid4().hex[:4]}",
                    title=f"Draft {i}",
                    description=None,
                    deep_link_url=None,
                    landing_url=None,
                    price_cents=None,
                    currency="USD",
                    status="draft",
                )
            await repo.insert_product(
                advertiser_id=advertiser_id,
                sku=f"ACTIVE-{uuid4().hex[:4]}",
                title="Active",
                description=None,
                deep_link_url=None,
                landing_url=None,
                price_cents=None,
                currency="USD",
                status="active",
            )

            drafts = await repo.list_products(advertiser_id, status="draft", limit=50, offset=0)
            assert all(p.status == "draft" for p in drafts)
            assert len(drafts) >= 3

            actives = await repo.list_products(advertiser_id, status="active", limit=50, offset=0)
            assert all(p.status == "active" for p in actives)
            assert len(actives) >= 1

            page_one = await repo.list_products(advertiser_id, status=None, limit=2, offset=0)
            page_two = await repo.list_products(advertiser_id, status=None, limit=2, offset=2)
            assert len(page_one) == 2
            # Pages don't overlap.
            ids_one = {p.id for p in page_one}
            ids_two = {p.id for p in page_two}
            assert ids_one.isdisjoint(ids_two)
    finally:
        await _delete_advertiser_cascade(pool, advertiser_id)


@_DB_REQUIRED
async def test_delete_product_returns_true_only_when_row_removed(pool):
    advertiser_id = await _seed_advertiser(pool)
    other_id = await _seed_advertiser(pool)
    try:
        async with pool.acquire() as conn:
            repo = AdvertiserRepository(conn)
            product = await repo.insert_product(
                advertiser_id=advertiser_id,
                sku=f"DEL-{uuid4().hex[:6]}",
                title="To delete",
                description=None,
                deep_link_url=None,
                landing_url=None,
                price_cents=None,
                currency="USD",
                status="draft",
            )
            # Wrong advertiser → no-op.
            assert await repo.delete_product(other_id, product.id) is False
            # Right advertiser → removed.
            assert await repo.delete_product(advertiser_id, product.id) is True
            # Already removed → no-op.
            assert await repo.delete_product(advertiser_id, product.id) is False
    finally:
        await _delete_advertiser_cascade(pool, advertiser_id)
        await _delete_advertiser_cascade(pool, other_id)


@_DB_REQUIRED
async def test_assert_products_owned_returns_missing_ids(pool):
    advertiser_id = await _seed_advertiser(pool)
    try:
        async with pool.acquire() as conn:
            repo = AdvertiserRepository(conn)
            owned = await repo.insert_product(
                advertiser_id=advertiser_id,
                sku=f"OWN-{uuid4().hex[:6]}",
                title="Owned",
                description=None,
                deep_link_url=None,
                landing_url=None,
                price_cents=None,
                currency="USD",
                status="draft",
            )
            ghost = uuid4()
            missing = await repo.assert_products_owned(advertiser_id, [owned.id, ghost])
            assert missing == [ghost]
    finally:
        await _delete_advertiser_cascade(pool, advertiser_id)


# ---------------------------------------------------------------------------
# FeedRepository
# ---------------------------------------------------------------------------


@_DB_REQUIRED
async def test_load_session_user_round_trips(pool):
    user = f"user_test_{uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        repo = FeedRepository(conn)
        session_id = await repo.insert_feed_session(
            user_id=user,
            room_name=None,
            device_id=None,
            app_version=None,
            country=None,
        )
        try:
            assert await repo.load_session_user(session_id) == user
            assert await repo.load_session_user(uuid4()) is None
        finally:
            await conn.execute("DELETE FROM feed_sessions WHERE id = $1", session_id)


@_DB_REQUIRED
async def test_insert_feed_event_idempotency_on_event_id(pool):
    """Same event_id → first insert returns True, second returns False.

    Mirrors `/feed/event`'s contract: retries write at most one row.
    """
    user = f"user_test_{uuid4().hex[:8]}"
    persona_id = await _seed_persona(pool)
    try:
        async with pool.acquire() as conn:
            repo = FeedRepository(conn)
            session_id = await repo.insert_feed_session(
                user_id=user,
                room_name=None,
                device_id=None,
                app_version=None,
                country=None,
            )
            try:
                event_id = uuid4()
                first = await repo.insert_feed_event(
                    event_id=event_id,
                    session_id=session_id,
                    persona_id=persona_id,
                    product_id=None,
                    event_kind="impression",
                    value_ms=None,
                )
                assert first is True
                # Replay → no row written.
                second = await repo.insert_feed_event(
                    event_id=event_id,
                    session_id=session_id,
                    persona_id=persona_id,
                    product_id=None,
                    event_kind="impression",
                    value_ms=None,
                )
                assert second is False
            finally:
                await conn.execute("DELETE FROM feed_sessions WHERE id = $1", session_id)
    finally:
        await _delete_persona(pool, persona_id)


@_DB_REQUIRED
async def test_load_products_returns_media_in_position_order(pool):
    """`load_products` must order embedded media by `position` ascending."""
    advertiser_id = await _seed_advertiser(pool)
    try:
        async with pool.acquire() as conn:
            adv_repo = AdvertiserRepository(conn)
            product = await adv_repo.insert_product(
                advertiser_id=advertiser_id,
                sku=f"MED-{uuid4().hex[:6]}",
                title="With media",
                description=None,
                deep_link_url=None,
                landing_url=None,
                price_cents=None,
                currency="USD",
                status="draft",
            )
        # Seed in non-sequential order — repo should still return sorted.
        await _seed_product_media(pool, product.id, position=2, kind="image", url="https://x/2.jpg")
        await _seed_product_media(pool, product.id, position=0, kind="image", url="https://x/0.jpg")
        await _seed_product_media(pool, product.id, position=1, kind="video", url="https://x/1.mp4")

        async with pool.acquire() as conn:
            feed_repo = FeedRepository(conn)
            products = await feed_repo.load_products([product.id])
            loaded = products[product.id]
            assert [m.position for m in loaded.media] == [0, 1, 2]
            assert [m.kind for m in loaded.media] == ["image", "video", "image"]
    finally:
        await _delete_advertiser_cascade(pool, advertiser_id)


# ---------------------------------------------------------------------------
# Hermetic (no-DB) tests for empty-list short-circuits.
#
# These pure-logic branches must not issue a SQL call when the input is
# empty. A `_NeverCalledConn` raises on any `.fetch(...)` so the test
# fails loudly if a future edit removes the guard.
# ---------------------------------------------------------------------------


class _NeverCalledConn:
    """Conn double whose `fetch` / `fetchrow` / `execute` all error if invoked.

    Used to assert that the empty-list short-circuit branches in
    `FeedRepository` / `AdvertiserRepository` never reach the SQL layer.
    """

    async def fetch(self, *args: Any, **kwargs: Any) -> list[Any]:
        raise AssertionError("fetch() must not be called for empty input")

    async def fetchrow(self, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
        raise AssertionError("fetchrow() must not be called for empty input")

    async def execute(self, *args: Any, **kwargs: Any) -> str:
        raise AssertionError("execute() must not be called for empty input")


async def test_load_persona_metas_short_circuits_on_empty_input() -> None:
    repo = FeedRepository(_NeverCalledConn())  # type: ignore[arg-type]
    assert await repo.load_persona_metas([]) == {}


async def test_load_products_short_circuits_on_empty_input() -> None:
    repo = FeedRepository(_NeverCalledConn())  # type: ignore[arg-type]
    assert await repo.load_products([]) == {}


async def test_assert_products_owned_short_circuits_on_empty_input() -> None:
    repo = AdvertiserRepository(_NeverCalledConn())  # type: ignore[arg-type]
    assert await repo.assert_products_owned(uuid4(), []) == []


def test_persona_meta_row_is_frozen_dataclass() -> None:
    """`PersonaMetaRow` is value-typed — guard against accidental mutation."""
    row = PersonaMetaRow(name="x", avatar_url=None)
    with pytest.raises((AttributeError, Exception)):
        row.name = "y"  # type: ignore[misc]
