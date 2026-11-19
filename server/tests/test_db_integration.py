"""Integration test: insert + read against the live Neon database.

Skips when ``DATABASE_URL`` is not set so the default ``uv run pytest`` stays
hermetic. Run with:

    DATABASE_URL="$DATABASE_URL" uv run pytest tests/test_db_integration.py

The test creates a throwaway user + app_state row keyed by a fresh UUID,
reads them back through the pool, and cleans up after itself.
"""

from __future__ import annotations

import json
import os
import uuid

import pytest

from soshop_server.db.pool import close_pool, get_pool

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set — skipping live Neon integration test",
)


async def test_user_and_app_state_round_trip() -> None:
    pool = await get_pool()
    external_user_id = f"user_test_{uuid.uuid4().hex}"
    state_key = "avatar_prefs"
    state_value = {"voice": "calm", "tone": "warm"}

    try:
        async with pool.acquire() as conn, conn.transaction():
            user_id = await conn.fetchval(
                """
                INSERT INTO users (external_user_id, email, display_name)
                VALUES ($1, $2, $3)
                RETURNING id
                """,
                external_user_id,
                f"{external_user_id}@example.com",
                "Test User",
            )
            assert user_id is not None

            await conn.execute(
                """
                INSERT INTO app_state (user_id, key, value)
                VALUES ($1, $2, $3::jsonb)
                """,
                user_id,
                state_key,
                json.dumps(state_value),
            )

            row = await conn.fetchrow(
                """
                SELECT u.external_user_id, s.key, s.value
                FROM users u
                JOIN app_state s ON s.user_id = u.id
                WHERE u.id = $1
                """,
                user_id,
            )
            assert row is not None
            assert row["external_user_id"] == external_user_id
            assert row["key"] == state_key
            assert json.loads(row["value"]) == state_value

            # Clean up — ON DELETE CASCADE removes the app_state row too.
            await conn.execute("DELETE FROM users WHERE id = $1", user_id)
    finally:
        await close_pool()
