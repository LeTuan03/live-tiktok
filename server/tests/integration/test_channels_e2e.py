"""End-to-end smoke test for the channel rotation surface.

Walks the full path the channel feed depends on:

1. ``reconcile_channels(2)`` against real LiveKit + real Neon: creates
   two LiveKit rooms, dispatches the agent into each, and upserts
   the matching ``channels`` rows.
2. ``GET /channels`` — asserts the public API returns two entries,
   each with a non-empty viewer token and an echoed ``livekit_url``.

Gated on both ``DATABASE_URL`` and ``LIVEKIT_URL`` being set. The
per-unit assertions (stride math, dispatch reuse, viewer-token grant
shape) are covered by the unit tests; this file is the
smoke layer that proves the wiring holds when the real services are in
the loop.

The schema migration (`migrations/0001_initial_schema.up.sql`) must already
be applied for the `channels` / `ads` tables to exist.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("DATABASE_URL"),
        reason="DATABASE_URL not set — skipping channels e2e test",
    ),
    pytest.mark.skipif(
        not os.environ.get("LIVEKIT_URL"),
        reason="LIVEKIT_URL not set — skipping channels e2e test",
    ),
]


async def _drop_test_channels() -> None:
    """Best-effort cleanup of the channel rows this test creates.

    Removes the ``channel_ad_log`` rows (CASCADE) and the ``channels``
    rows that live at ordinals 0/1, so a repeat run of the test (or a
    follow-on test that asserts an empty channels table) starts from a
    known baseline. LiveKit rooms are left to reap themselves via
    ``empty_timeout`` — the test does not own teardown of LiveKit
    resources.
    """
    from soshop_server.db.pool import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM channels WHERE id = ANY($1::text[])",
            ["channel-0", "channel-1"],
        )


async def test_reconcile_then_list_returns_two_channels_with_tokens() -> None:
    from soshop.livekit.channels import reconcile_channels
    from soshop_server.api.app import create_app
    from soshop_server.db.pool import close_pool

    # Start from a clean baseline so the reconcile is the only thing
    # populating the channels table for this test. Don't touch `ads` —
    # the rotation seed lives in migration 0004 and the e2e relies on
    # it being present.
    await _drop_test_channels()

    try:
        channels = await reconcile_channels(2)
        assert len(channels) == 2
        assert [c.ordinal for c in channels] == [0, 1]
        assert {c.id for c in channels} == {"channel-0", "channel-1"}

        app = create_app()
        # Bypass the lifespan reconcile — we already reconciled above
        # and TestClient would otherwise re-run the startup hook.
        with TestClient(app) as client:
            resp = client.get("/channels")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body) == 2
        assert {c["id"] for c in body} == {"channel-0", "channel-1"}
        for entry in body:
            assert entry["livekit_url"], f"missing livekit_url on {entry['id']}"
            assert entry["livekit_token"], f"missing livekit_token on {entry['id']}"
    finally:
        await _drop_test_channels()
        await close_pool()
