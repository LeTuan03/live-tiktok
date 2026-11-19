"""The shop's product catalog, as used by the promo video flow.

Backed by the existing ``products`` table (plus the fields added in migration
0003) with the feature image in ``product_media`` position 0. Raw SQL through
asyncpg like the rest of the server.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any

import asyncpg
from pydantic import BaseModel

from soshop.services.studio import to_uuid
from soshop_server.core.config import settings

_PLACEHOLDER_HOSTS = ("picsum.photos", "placehold.co", "via.placeholder.com")
"""Seed images that are random stock photos, not pictures of the product."""


class CatalogProduct(BaseModel):
    id: str
    sku: str
    name: str
    brand: str
    category: str
    price: float | None
    currency: str
    description: str
    highlights: list[str]
    usage: str
    talking_points: str
    """What the shop wants said (promotions, gifts, guarantees)."""
    image_url: str | None
    gallery: list[str]
    image_is_placeholder: bool
    status: str
    created_at: datetime


_COLUMNS = """
    p.id, p.sku, p.title, p.brand, p.category, p.price_cents, p.currency,
    p.description, p.highlights, p.usage_notes, p.talking_points, p.status, p.created_at,
    COALESCE(
        (SELECT array_agg(m.url ORDER BY m.position) FROM product_media m
         WHERE m.product_id = p.id AND m.kind = 'image'),
        '{}'
    ) AS images
"""


def split_lines(text: str | None) -> list[str]:
    return [line.strip(" -•\t") for line in (text or "").splitlines() if line.strip(" -•\t")]


def is_placeholder(url: str | None) -> bool:
    return bool(url) and any(host in url for host in _PLACEHOLDER_HOSTS)  # type: ignore[operator]


def _from_record(row: asyncpg.Record) -> CatalogProduct:
    images = list(row["images"] or [])
    image = images[0] if images else None
    cents = row["price_cents"]
    return CatalogProduct(
        id=str(row["id"]),
        sku=row["sku"],
        name=row["title"],
        brand=row["brand"] or "",
        category=row["category"] or "",
        price=cents / 100 if cents is not None else None,
        currency=(row["currency"] or "VND").upper(),
        description=row["description"] or "",
        highlights=split_lines(row["highlights"]),
        usage=row["usage_notes"] or "",
        talking_points=row["talking_points"] or "",
        image_url=image,
        gallery=images[1:],
        image_is_placeholder=image is None or is_placeholder(image),
        status=row["status"],
        created_at=row["created_at"],
    )


async def list_products(conn: asyncpg.Connection) -> list[CatalogProduct]:
    rows = await conn.fetch(
        f"SELECT {_COLUMNS} FROM products p "  # noqa: S608 - constant column list
        "WHERE p.status <> 'archived' ORDER BY p.created_at DESC, p.title"
    )
    return [_from_record(r) for r in rows]


async def get_product(conn: asyncpg.Connection, product_id: str) -> CatalogProduct | None:
    pid = to_uuid(product_id)
    if pid is None:
        return None
    row = await conn.fetchrow(
        f"SELECT {_COLUMNS} FROM products p WHERE p.id = $1",  # noqa: S608
        pid,
    )
    return _from_record(row) if row else None


async def get_many(conn: asyncpg.Connection, ids: set[str]) -> dict[str, CatalogProduct]:
    """Products by id, archived ones included (old videos still show them)."""
    uuids = [u for u in (to_uuid(i) for i in ids) if u is not None]
    if not uuids:
        return {}
    rows = await conn.fetch(
        f"SELECT {_COLUMNS} FROM products p WHERE p.id = ANY($1::uuid[])",  # noqa: S608
        uuids,
    )
    return {str(r["id"]): _from_record(r) for r in rows}


async def _advertiser_id(conn: asyncpg.Connection) -> Any:
    """The shop's advertiser row: the default org, else any, else a new one."""
    row = await conn.fetchrow(
        "SELECT id FROM advertisers WHERE org_id = $1", settings.DEFAULT_ORG_ID
    ) or await conn.fetchrow("SELECT id FROM advertisers ORDER BY created_at LIMIT 1")
    if row:
        return row["id"]
    return await conn.fetchval(
        "INSERT INTO advertisers (org_id, name) VALUES ($1, 'SoShop') RETURNING id",
        settings.DEFAULT_ORG_ID,
    )


def _cents(price: float | None) -> int | None:
    return round(price * 100) if price is not None else None


_PATCHABLE = {
    "name": "title",
    "brand": "brand",
    "category": "category",
    "description": "description",
    "usage": "usage_notes",
    "talking_points": "talking_points",
}


async def create_product(conn: asyncpg.Connection, fields: dict[str, Any]) -> CatalogProduct:
    async with conn.transaction():
        advertiser = await _advertiser_id(conn)
        pid = await conn.fetchval(
            """
            INSERT INTO products
                (advertiser_id, sku, title, brand, category, description, highlights,
                 usage_notes, talking_points, price_cents, currency, status)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 'active')
            RETURNING id
            """,
            advertiser,
            f"SS-{secrets.token_hex(3).upper()}",
            fields["name"],
            fields.get("brand") or "",
            fields.get("category") or "",
            fields.get("description") or "",
            "\n".join(fields.get("highlights") or []),
            fields.get("usage") or "",
            fields.get("talking_points") or "",
            _cents(fields.get("price")),
            (fields.get("currency") or "VND").upper(),
        )
        if fields.get("image_url"):
            await _set_image(conn, pid, fields["image_url"])
    product = await get_product(conn, str(pid))
    if product is None:
        raise RuntimeError("product vanished right after insert")
    return product


async def update_product(
    conn: asyncpg.Connection, product_id: str, fields: dict[str, Any]
) -> CatalogProduct | None:
    pid = to_uuid(product_id)
    if pid is None:
        return None
    sets: list[str] = []
    args: list[Any] = []
    for key, column in _PATCHABLE.items():
        if key in fields and fields[key] is not None:
            args.append(fields[key])
            sets.append(f"{column} = ${len(args)}")
    if "highlights" in fields:
        args.append("\n".join(fields["highlights"] or []))
        sets.append(f"highlights = ${len(args)}")
    if "price" in fields:
        args.append(_cents(fields["price"]))
        sets.append(f"price_cents = ${len(args)}")
    if fields.get("currency"):
        args.append(fields["currency"].upper())
        sets.append(f"currency = ${len(args)}")
    async with conn.transaction():
        if sets:
            args.append(pid)
            result = await conn.execute(
                f"UPDATE products SET {', '.join(sets)}, updated_at = NOW() "  # noqa: S608
                f"WHERE id = ${len(args)}",
                *args,
            )
            if result.endswith(" 0"):
                return None
        if fields.get("image_url"):
            await _set_image(conn, pid, fields["image_url"])
    return await get_product(conn, product_id)


async def _set_image(conn: asyncpg.Connection, pid: Any, url: str) -> None:
    """Make ``url`` the feature image (position 0)."""
    await conn.execute(
        """
        INSERT INTO product_media (product_id, position, kind, url)
        VALUES ($1, 0, 'image', $2)
        ON CONFLICT (product_id, position)
        DO UPDATE SET url = EXCLUDED.url, kind = 'image', updated_at = NOW()
        """,
        pid,
        url,
    )


async def archive_product(conn: asyncpg.Connection, product_id: str) -> bool:
    """Hide a product. Rows stay because ads and feed events reference them."""
    pid = to_uuid(product_id)
    if pid is None:
        return False
    result = await conn.execute(
        "UPDATE products SET status = 'archived', updated_at = NOW() WHERE id = $1", pid
    )
    return not result.endswith(" 0")
