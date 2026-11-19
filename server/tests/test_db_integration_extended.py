"""Extended DB integration tests against a real Postgres.

Skips automatically when ``DATABASE_URL`` is not set so the default
``uv run pytest`` stays hermetic. Run with the variable exported (Neon
test branch or a local Postgres):

    DATABASE_URL="$DATABASE_URL" uv run pytest tests/test_db_integration_extended.py

Each test creates throwaway rows in a transaction and rolls them back so
the test branch never accumulates fixture data. Tests cover the
read/write paths the API actually uses:

* advertiser → product → campaign → campaign_products linkage
* persona + persona_script + favorite (toggle path)
* feed_session + feed_event idempotent INSERT (`ON CONFLICT DO NOTHING`)
* enum CHECK constraints actually fire on bad inputs

The user has been clear elsewhere that mocked tests can hide migration
breakage — these tests deliberately exercise the live schema.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

from soshop_server.db.pool import close_pool, get_pool

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set — skipping live Postgres integration tests",
)


@pytest.fixture
async def conn() -> AsyncIterator[Any]:
    """Yield a connection inside a transaction that is rolled back at the end.

    Tests that need to commit (e.g. to test cross-connection visibility)
    should acquire their own connection via the pool — but the round-trip
    paths exercised here are within-connection, so a rollback fixture
    keeps the test branch clean.
    """
    pool = await get_pool()
    async with pool.acquire() as c:
        txn = c.transaction()
        await txn.start()
        try:
            yield c
        finally:
            await txn.rollback()


@pytest.fixture(scope="module", autouse=True)
async def _close_pool_after_module() -> AsyncIterator[None]:
    yield
    await close_pool()


# ---------------------------------------------------------------------------
# Advertiser → product → campaign linkage
# ---------------------------------------------------------------------------


async def test_advertiser_product_campaign_round_trip(conn: Any) -> None:
    """Insert an advertiser + product + campaign + campaign_products link
    and read them back. Verifies the FK chain the advertiser portal relies on."""
    org_id = f"org_test_{uuid.uuid4().hex}"
    advertiser_id = await conn.fetchval(
        """
        INSERT INTO advertisers (org_id, name, contact_email)
        VALUES ($1, $2, $3) RETURNING id
        """,
        org_id,
        "Acme Test",
        "ops@acme.test",
    )

    product_id = await conn.fetchval(
        """
        INSERT INTO products (advertiser_id, sku, title, status)
        VALUES ($1, $2, $3, 'draft') RETURNING id
        """,
        advertiser_id,
        f"sku-{uuid.uuid4().hex[:8]}",
        "Test Cap",
    )

    campaign_id = await conn.fetchval(
        """
        INSERT INTO campaigns (advertiser_id, name, budget_cents, status, targeting)
        VALUES ($1, $2, $3, 'draft', '{}'::jsonb) RETURNING id
        """,
        advertiser_id,
        "Spring",
        100_000,
    )

    await conn.execute(
        """
        INSERT INTO campaign_products (campaign_id, product_id)
        VALUES ($1, $2)
        """,
        campaign_id,
        product_id,
    )

    rows = await conn.fetch(
        """
        SELECT c.id AS campaign_id, c.name, p.id AS product_id, p.sku
        FROM campaigns c
        JOIN campaign_products cp ON cp.campaign_id = c.id
        JOIN products p ON p.id = cp.product_id
        WHERE c.advertiser_id = $1
        """,
        advertiser_id,
    )
    assert len(rows) == 1
    assert rows[0]["campaign_id"] == campaign_id
    assert rows[0]["product_id"] == product_id


async def test_product_unique_sku_per_advertiser(conn: Any) -> None:
    """`(advertiser_id, sku)` is unique — duplicate inserts must raise."""
    import asyncpg

    org_id = f"org_dup_{uuid.uuid4().hex}"
    advertiser_id = await conn.fetchval(
        "INSERT INTO advertisers (org_id, name) VALUES ($1, $2) RETURNING id",
        org_id,
        "Dupes",
    )
    sku = f"sku-{uuid.uuid4().hex[:8]}"
    await conn.execute(
        "INSERT INTO products (advertiser_id, sku, title) VALUES ($1, $2, $3)",
        advertiser_id,
        sku,
        "First",
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await conn.execute(
            "INSERT INTO products (advertiser_id, sku, title) VALUES ($1, $2, $3)",
            advertiser_id,
            sku,
            "Second",
        )


# ---------------------------------------------------------------------------
# Persona + script + favorite
# ---------------------------------------------------------------------------


async def test_persona_script_and_favorite_round_trip(conn: Any) -> None:
    persona_id = await conn.fetchval(
        """
        INSERT INTO personas (name, voice_pipeline_id, status)
        VALUES ($1, 'pipeline_v1', 'active') RETURNING id
        """,
        f"P-{uuid.uuid4().hex[:6]}",
    )
    script_id = await conn.fetchval(
        """
        INSERT INTO persona_scripts (persona_id, script_text, weight)
        VALUES ($1, $2, $3) RETURNING id
        """,
        persona_id,
        "Buy this thing",
        100,
    )

    user_id = f"user_test_{uuid.uuid4().hex}"
    await conn.execute(
        "INSERT INTO favorites (user_id, persona_id) VALUES ($1, $2)",
        user_id,
        persona_id,
    )

    # The favorites listing query the route uses.
    rows = await conn.fetch(
        """
        SELECT p.id, p.name, f.created_at
        FROM favorites f
        JOIN personas p ON p.id = f.persona_id
        WHERE f.user_id = $1
        """,
        user_id,
    )
    assert len(rows) == 1
    assert rows[0]["id"] == persona_id

    # Idempotent INSERT — duplicate must be a no-op (PK on user_id, persona_id).
    res = await conn.execute(
        """
        INSERT INTO favorites (user_id, persona_id) VALUES ($1, $2)
        ON CONFLICT DO NOTHING
        """,
        user_id,
        persona_id,
    )
    assert res.endswith(" 0")  # zero rows affected on conflict

    # Toggle off.
    await conn.execute(
        "DELETE FROM favorites WHERE user_id = $1 AND persona_id = $2",
        user_id,
        persona_id,
    )
    remaining = await conn.fetchval(
        "SELECT COUNT(*) FROM favorites WHERE user_id = $1",
        user_id,
    )
    assert remaining == 0
    # touch script_id so the linter sees the variable as used.
    assert isinstance(script_id, uuid.UUID)


# ---------------------------------------------------------------------------
# Feed session + idempotent feed_event
# ---------------------------------------------------------------------------


async def test_feed_event_idempotent_insert(conn: Any) -> None:
    """Mirrors `_record_feed_event_idempotent` — the second insert must
    return INSERT 0 0 thanks to the PK conflict."""
    user = f"user_test_{uuid.uuid4().hex}"
    persona_id = await conn.fetchval(
        """
        INSERT INTO personas (name, voice_pipeline_id, status)
        VALUES ($1, 'pipeline_v1', 'active') RETURNING id
        """,
        f"P-{uuid.uuid4().hex[:6]}",
    )
    session_id = await conn.fetchval(
        """
        INSERT INTO feed_sessions (user_id) VALUES ($1) RETURNING id
        """,
        user,
    )
    event_id = uuid.uuid4()

    res1 = await conn.execute(
        """
        INSERT INTO feed_events (id, session_id, persona_id, event_kind, value_ms)
        VALUES ($1, $2, $3, 'tap', 1500)
        ON CONFLICT (id) DO NOTHING
        """,
        event_id,
        session_id,
        persona_id,
    )
    res2 = await conn.execute(
        """
        INSERT INTO feed_events (id, session_id, persona_id, event_kind, value_ms)
        VALUES ($1, $2, $3, 'tap', 1500)
        ON CONFLICT (id) DO NOTHING
        """,
        event_id,
        session_id,
        persona_id,
    )
    assert res1.endswith(" 1")
    assert res2.endswith(" 0")

    rows = await conn.fetch(
        "SELECT id FROM feed_events WHERE session_id = $1",
        session_id,
    )
    assert len(rows) == 1


async def test_feed_event_kind_check_constraint(conn: Any) -> None:
    """Bad enum values must be rejected by the CHECK constraint."""
    import asyncpg

    user = f"user_test_{uuid.uuid4().hex}"
    persona_id = await conn.fetchval(
        """
        INSERT INTO personas (name, voice_pipeline_id, status)
        VALUES ($1, 'pipeline_v1', 'active') RETURNING id
        """,
        f"P-{uuid.uuid4().hex[:6]}",
    )
    session_id = await conn.fetchval(
        "INSERT INTO feed_sessions (user_id) VALUES ($1) RETURNING id",
        user,
    )
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(
            """
            INSERT INTO feed_events (session_id, persona_id, event_kind)
            VALUES ($1, $2, 'NOT_A_KIND')
            """,
            session_id,
            persona_id,
        )


# ---------------------------------------------------------------------------
# Cascade behavior — proves the schema's ON DELETE rules fire as expected
# ---------------------------------------------------------------------------


async def test_persona_delete_cascades_favorites_and_scripts(conn: Any) -> None:
    """Deleting a persona removes its scripts and any user favorites.

    The route layer assumes this cascade — without it, deleting a persona
    would leave dangling rows that the favorites/clips queries could
    crash on.
    """
    persona_id = await conn.fetchval(
        """
        INSERT INTO personas (name, voice_pipeline_id, status)
        VALUES ($1, 'pipeline_v1', 'active') RETURNING id
        """,
        f"P-{uuid.uuid4().hex[:6]}",
    )
    await conn.execute(
        """
        INSERT INTO persona_scripts (persona_id, script_text, weight)
        VALUES ($1, $2, $3)
        """,
        persona_id,
        "Hello",
        1,
    )
    user = f"user_test_{uuid.uuid4().hex}"
    await conn.execute(
        "INSERT INTO favorites (user_id, persona_id) VALUES ($1, $2)",
        user,
        persona_id,
    )

    await conn.execute("DELETE FROM personas WHERE id = $1", persona_id)
    scripts = await conn.fetchval(
        "SELECT COUNT(*) FROM persona_scripts WHERE persona_id = $1", persona_id
    )
    favs = await conn.fetchval("SELECT COUNT(*) FROM favorites WHERE persona_id = $1", persona_id)
    assert scripts == 0
    assert favs == 0
