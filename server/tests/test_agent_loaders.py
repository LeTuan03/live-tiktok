"""Hermetic-by-default tests for the DB-backed agent loaders.

Skipped when `DATABASE_URL` is unset (CI default). Locally they seed
their own rows and clean up in a `try/finally` so a Neon branch isn't
polluted between runs.

We can't rely on transaction-rollback inside a test because the
loaders open their own pooled connection separate from the inserter's
connection — pgbouncer in transaction-pool mode wouldn't even see the
in-flight transaction. Instead we commit, run the assertions, and
delete in `finally`.
"""

from __future__ import annotations

import os
from uuid import uuid4

import asyncpg
import pytest

from soshop.agent.loaders import (
    load_persona_profile,
    load_persona_voice_id,
    load_product_context,
    load_script_body,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set — integration loaders skipped",
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


async def _seed_persona(pool, *, name="Loader Persona"):
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
            "voice-elevenlabs-x",
            "elevenlabs-eleven_turbo_v2_5",
            "calm and measured",
            "ten years on the trail",
            "evergreen, practical",
        )


async def _delete_persona(pool, persona_id):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM personas WHERE id = $1", persona_id)


async def _seed_product(pool):
    async with pool.acquire() as conn:
        advertiser_id = await conn.fetchval(
            "INSERT INTO advertisers (org_id, name) VALUES ($1, $2) RETURNING id",
            f"org_test_{uuid4().hex[:8]}",
            "Loader Test Co.",
        )
        product_id = await conn.fetchval(
            """
            INSERT INTO products
                (advertiser_id, sku, title, description,
                 price_cents, currency, status)
            VALUES ($1, $2, $3, $4, $5, $6, 'active')
            RETURNING id
            """,
            advertiser_id,
            f"SKU-{uuid4().hex[:6]}",
            "Test Earbuds",
            "Hybrid ANC, 10h buds.",
            12_900,
            "USD",
        )
    return advertiser_id, product_id


async def _delete_product(pool, advertiser_id):
    async with pool.acquire() as conn:
        # Cascade deletes products via advertiser_id FK.
        await conn.execute("DELETE FROM advertisers WHERE id = $1", advertiser_id)


async def _seed_script(pool, persona_id, product_id):
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO persona_scripts
                (persona_id, product_id, script_text, hook, cta, weight)
            VALUES ($1, $2, $3, $4, $5, 1)
            RETURNING id
            """,
            persona_id,
            product_id,
            "These are the earbuds I actually wear on long runs.",
            "If you only buy one pair this year",
            "Tap to shop · $129",
        )


async def test_load_persona_profile_maps_personality_into_voice_tone(pool):
    persona_id = await _seed_persona(pool)
    try:
        profile = await load_persona_profile(pool, str(persona_id))
        assert profile["name"] == "Loader Persona"
        assert profile["voice_tone"] == "calm and measured"
        assert profile["traits"] == "evergreen, practical"
    finally:
        await _delete_persona(pool, persona_id)


async def test_load_persona_profile_returns_fallback_for_unknown_id(pool):
    profile = await load_persona_profile(pool, str(uuid4()))
    assert profile == {
        "name": "host",
        "backstory": "none",
        "traits": "none",
        "voice_tone": "none",
    }


async def test_load_persona_profile_returns_fallback_for_invalid_uuid(pool):
    profile = await load_persona_profile(pool, "not-a-uuid")
    assert profile["voice_tone"] == "none"


async def test_load_script_body_renders_hook_text_and_cta_inline(pool):
    """Pitch is one contiguous block — hook → body → CTA, no labels.

    The structured rendering (``Hook:`` / ``Call-to-action (deliver
    verbatim): "..."``) was cuing the LLM to deliver each labeled
    section as its own utterance, padding the pitch ~3× over the 15 s
    target. A flat join keeps the verbatim CTA in the prompt without
    the section-header cue.
    """
    advertiser_id, product_id = await _seed_product(pool)
    persona_id = await _seed_persona(pool)
    try:
        script_id = await _seed_script(pool, persona_id, product_id)
        body = await load_script_body(pool, str(script_id))
        assert (
            body == "If you only buy one pair this year These are the earbuds I "
            "actually wear on long runs. Tap to shop · $129"
        )
    finally:
        await _delete_persona(pool, persona_id)
        await _delete_product(pool, advertiser_id)


async def test_load_script_body_returns_none_for_unknown_script(pool):
    body = await load_script_body(pool, str(uuid4()))
    assert body == "none"


async def test_load_product_context_returns_dict(pool):
    advertiser_id, product_id = await _seed_product(pool)
    try:
        ctx = await load_product_context(pool, str(product_id))
        assert ctx is not None
        assert ctx["product_id"] == str(product_id)
        assert ctx["title"] == "Test Earbuds"
        assert ctx["price"] == "129.00 USD"
    finally:
        await _delete_product(pool, advertiser_id)


async def test_load_product_context_returns_none_for_no_id(pool):
    assert await load_product_context(pool, None) is None


async def test_load_persona_voice_id_returns_voice_and_avatar(pool):
    persona_id = await _seed_persona(pool)
    try:
        voice_id, avatar = await load_persona_voice_id(pool, str(persona_id))
        assert voice_id == "voice-elevenlabs-x"
        assert avatar == "https://example/avatar.jpg"
    finally:
        await _delete_persona(pool, persona_id)


async def test_load_persona_voice_id_unknown_id(pool):
    voice_id, avatar = await load_persona_voice_id(pool, str(uuid4()))
    assert voice_id is None
    assert avatar is None
