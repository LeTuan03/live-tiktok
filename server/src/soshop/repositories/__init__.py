"""Repository layer — keeps raw SQL out of route handlers.

Each repository wraps an asyncpg connection and exposes a small set of
async methods that route handlers call. Repositories are intentionally
thin (no caching, no transactions across calls) — they exist to make
route code easier to read and to let tests target SQL composition
directly when needed.
"""

from __future__ import annotations

from soshop.repositories.advertiser import AdvertiserRepository
from soshop.repositories.feed import FeedRepository

__all__ = [
    "AdvertiserRepository",
    "FeedRepository",
]
