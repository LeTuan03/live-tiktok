"""Livestream sessions made of pre-rendered videos ("phiên livestream").

Data access for ``broadcasts`` and ``broadcast_items`` (migration 0005) plus
the control actions. The moving playback position is never stored: only the
``playhead`` anchor is, and :mod:`soshop.broadcast.timeline` derives the rest.
Every write that can change what plays runs under a row lock and re-anchors
the playhead to "now" against the list as it was before the write.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import asyncpg
from pydantic import BaseModel

from soshop.broadcast import media
from soshop.broadcast.control import ON_AIR, Action, ControlError, Status, plan
from soshop.broadcast.hub import hub
from soshop.broadcast.timeline import (
    LoopMode,
    Phase,
    Playhead,
    Slot,
    Timeline,
    locate,
    reanchor,
)
from soshop.promo import jobs
from soshop.services import catalog
from soshop.services.studio import to_uuid
from soshop.services.studio_assets import data_dir

ItemRole = Literal["intro", "product", "outro"]
ItemSource = Literal["promo", "upload"]

MAX_PRODUCT_ITEMS = 100
_DEFAULT_TITLE: dict[str, str] = {
    "intro": "Video mở đầu",
    "outro": "Video kết thúc",
    "product": "Video sản phẩm",
}


class BroadcastError(Exception):
    """A request the broadcast cannot honour; ``status_code`` is the HTTP status."""

    def __init__(self, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# -------------------------------------------------------------------- shapes


class ItemProduct(BaseModel):
    id: str
    name: str
    price: float | None
    currency: str
    image_url: str | None


class BroadcastItem(BaseModel):
    id: str
    role: ItemRole
    position: int
    source: ItemSource
    source_ref: str
    label: str
    title: str
    """What to call the item on screen: its label, else the product name."""
    duration_s: int | None
    """Time on air set by the user; ``None`` plays the video once."""
    video_s: float | None
    """The video's own length."""
    slot_s: float | None
    """Effective time on air, ``None`` while the item cannot play."""
    playable: bool
    problem: str | None
    video_url: str | None
    thumb_url: str | None
    captions_url: str | None
    product: ItemProduct | None
    host_name: str | None


class NowPlaying(BaseModel):
    phase: Phase
    item_id: str
    index: int
    cycle: int
    offset_ms: int
    length_ms: int


class Playback(BaseModel):
    status: Status
    playhead: Playhead | None
    position: NowPlaying | None
    """Where the show is at ``server_time_ms``."""
    server_time_ms: int
    started_at: datetime | None
    ended_at: datetime | None


class Broadcast(BaseModel):
    id: str
    title: str
    notes: str
    loop_mode: LoopMode
    loop_count: int
    intro: BroadcastItem | None
    outro: BroadcastItem | None
    items: list[BroadcastItem]
    cycle_s: float
    """One pass through the playable product videos."""
    total_s: float | None
    """The whole show, ``None`` when it loops until stopped."""
    playback: Playback
    created_at: datetime
    updated_at: datetime


class BroadcastSummary(BaseModel):
    id: str
    title: str
    status: Status
    loop_mode: LoopMode
    loop_count: int
    item_count: int
    playable_count: int
    cycle_s: float
    thumb_url: str | None
    now_playing: str | None
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ItemSpec:
    """One video to add."""

    role: ItemRole
    source: ItemSource
    source_ref: str
    duration_s: int | None = None
    label: str = ""
    product_id: str | None = None
    """Product an uploaded video presents. Promo videos bring their own."""


# ------------------------------------------------------------------- reading

_COLUMNS = (
    "id, title, notes, loop_mode, loop_count, status, playhead, "
    "started_at, ended_at, created_at, updated_at"
)
_ITEM_COLUMNS = (
    "id, broadcast_id, role, position, source, source_ref, source_duration_s, "
    "product_id, label, duration_s"
)


def now_ms() -> int:
    return int(time.time() * 1000)


def _require_uuid(value: str, what: str = "phiên livestream") -> UUID:
    uid = to_uuid(value)
    if uid is None:
        raise BroadcastError(f"Không tìm thấy {what}.", status_code=404)
    return uid


def _parse_playhead(raw: Any) -> Playhead | None:
    if raw is None:
        return None
    try:
        if isinstance(raw, str):
            return Playhead.model_validate_json(raw)
        return Playhead.model_validate(raw)
    except ValueError:
        return None


def _media(row: asyncpg.Record) -> media.Media:
    if row["source"] == "promo":
        return media.resolve_promo(row["source_ref"])
    return media.resolve_upload(row["source_ref"], row["source_duration_s"])


def _product(
    row: asyncpg.Record, found: media.Media, products: dict[str, catalog.CatalogProduct]
) -> ItemProduct | None:
    """The catalog product as it is now, else the promo video's snapshot."""
    pid = str(row["product_id"]) if row["product_id"] else None
    if pid is None and found.product is not None:
        pid = found.product.id
    fresh = products.get(pid) if pid else None
    if fresh is not None:
        image = None if fresh.image_is_placeholder else fresh.image_url
        return ItemProduct(
            id=fresh.id,
            name=fresh.name,
            price=fresh.price,
            currency=fresh.currency,
            image_url=image,
        )
    snap = found.product
    if snap is None:
        return None
    return ItemProduct(
        id=snap.id,
        name=snap.name,
        price=snap.price,
        currency=snap.currency,
        image_url=snap.image_url,
    )


def _item(row: asyncpg.Record, products: dict[str, catalog.CatalogProduct]) -> BroadcastItem:
    found = _media(row)
    product = _product(row, found, products)
    duration = row["duration_s"]
    playable = found.playable
    role: ItemRole = row["role"]
    return BroadcastItem(
        id=str(row["id"]),
        role=role,
        position=row["position"],
        source=row["source"],
        source_ref=row["source_ref"],
        label=row["label"],
        title=row["label"] or (product.name if product else "") or _DEFAULT_TITLE[role],
        duration_s=duration,
        video_s=found.video_s,
        slot_s=(float(duration) if duration else found.video_s) if playable else None,
        playable=playable,
        problem=found.problem,
        video_url=found.video_url,
        thumb_url=found.thumb_url,
        captions_url=found.captions_url,
        product=product,
        host_name=found.host_name,
    )


def _slot(item: BroadcastItem | None) -> Slot | None:
    if item is None or not item.playable or not item.slot_s:
        return None
    return Slot(item.id, max(round(item.slot_s * 1000), 1))


def build_timeline(
    intro: BroadcastItem | None,
    items: Iterable[BroadcastItem],
    outro: BroadcastItem | None,
    loop_mode: LoopMode,
    loop_count: int,
) -> Timeline:
    """The playable part of a playlist. Items that cannot play are skipped."""
    return Timeline(
        items=tuple(slot for slot in map(_slot, items) if slot is not None),
        intro=_slot(intro),
        outro=_slot(outro),
        loop_mode=loop_mode,
        loop_count=loop_count,
    )


def _assemble(
    row: asyncpg.Record,
    item_rows: list[asyncpg.Record],
    products: dict[str, catalog.CatalogProduct],
    at_ms: int,
) -> Broadcast:
    items = [_item(r, products) for r in item_rows]
    intro = next((i for i in items if i.role == "intro"), None)
    outro = next((i for i in items if i.role == "outro"), None)
    body = [i for i in items if i.role == "product"]
    timeline = build_timeline(intro, body, outro, row["loop_mode"], row["loop_count"])
    status: Status = row["status"]
    head = _parse_playhead(row["playhead"]) if status in ON_AIR else None
    where = locate(timeline, head, at_ms) if head is not None else None
    if status in ON_AIR and where is None:
        status, head = "ended", None  # ran off the end; persisted by the caller
    total = timeline.total_ms
    return Broadcast(
        id=str(row["id"]),
        title=row["title"],
        notes=row["notes"],
        loop_mode=row["loop_mode"],
        loop_count=row["loop_count"],
        intro=intro,
        outro=outro,
        items=body,
        cycle_s=timeline.cycle_ms / 1000,
        total_s=total / 1000 if total is not None else None,
        playback=Playback(
            status=status,
            playhead=head,
            position=NowPlaying(
                phase=where.phase,
                item_id=where.item_id,
                index=where.index,
                cycle=where.cycle,
                offset_ms=where.offset_ms,
                length_ms=where.length_ms,
            )
            if where is not None
            else None,
            server_time_ms=at_ms,
            started_at=row["started_at"],
            ended_at=row["ended_at"],
        ),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def _item_rows(conn: asyncpg.Connection, ids: list[UUID]) -> list[asyncpg.Record]:
    return list(
        await conn.fetch(
            f"SELECT {_ITEM_COLUMNS} FROM broadcast_items "  # noqa: S608 - constant columns
            "WHERE broadcast_id = ANY($1::uuid[]) ORDER BY position, created_at",
            ids,
        )
    )


async def _products_for(
    conn: asyncpg.Connection, item_rows: list[asyncpg.Record]
) -> dict[str, catalog.CatalogProduct]:
    """Current catalog rows; an item whose product is gone shows its video's snapshot."""
    return await catalog.get_many(
        conn, {str(r["product_id"]) for r in item_rows if r["product_id"]}
    )


async def _load(conn: asyncpg.Connection, rows: list[asyncpg.Record]) -> list[Broadcast]:
    """Full broadcasts for ``rows``, with any that ran off the end marked ended."""
    item_rows = await _item_rows(conn, [r["id"] for r in rows])
    products = await _products_for(conn, item_rows)
    at = now_ms()
    out: list[Broadcast] = []
    for row in rows:
        mine = [r for r in item_rows if r["broadcast_id"] == row["id"]]
        broadcast = _assemble(row, mine, products, at)
        if row["status"] in ON_AIR and broadcast.playback.status == "ended":
            await _mark_ended(conn, row["id"], row["playhead"])
        out.append(broadcast)
    return out


async def _mark_ended(conn: asyncpg.Connection, bid: UUID, seen_playhead: Any) -> None:
    """Persist a natural end, unless someone changed the playhead meanwhile."""
    await conn.execute(
        """
        UPDATE broadcasts
           SET status = 'ended', playhead = NULL, ended_at = NOW(), updated_at = NOW()
         WHERE id = $1 AND status IN ('live', 'paused')
           AND playhead IS NOT DISTINCT FROM $2::jsonb
        """,
        bid,
        seen_playhead,
    )


def summarize(broadcast: Broadcast) -> BroadcastSummary:
    first = next((i for i in broadcast.items if i.thumb_url), None)
    playing = broadcast.playback.position
    now_playing = None
    if playing is not None:
        current = [broadcast.intro, *broadcast.items, broadcast.outro]
        match = next((i for i in current if i is not None and i.id == playing.item_id), None)
        now_playing = match.title if match else None
    return BroadcastSummary(
        id=broadcast.id,
        title=broadcast.title,
        status=broadcast.playback.status,
        loop_mode=broadcast.loop_mode,
        loop_count=broadcast.loop_count,
        item_count=len(broadcast.items),
        playable_count=sum(1 for i in broadcast.items if i.playable),
        cycle_s=broadcast.cycle_s,
        thumb_url=first.thumb_url if first else None,
        now_playing=now_playing,
        started_at=broadcast.playback.started_at,
        ended_at=broadcast.playback.ended_at,
        created_at=broadcast.created_at,
        updated_at=broadcast.updated_at,
    )


async def list_broadcasts(conn: asyncpg.Connection) -> list[BroadcastSummary]:
    rows = list(
        await conn.fetch(
            f"SELECT {_COLUMNS} FROM broadcasts ORDER BY created_at DESC"  # noqa: S608
        )
    )
    return [summarize(b) for b in await _load(conn, rows)]


async def get_broadcast(conn: asyncpg.Connection, broadcast_id: str) -> Broadcast | None:
    bid = to_uuid(broadcast_id)
    if bid is None:
        return None
    row = await conn.fetchrow(
        f"SELECT {_COLUMNS} FROM broadcasts WHERE id = $1",  # noqa: S608
        bid,
    )
    if row is None:
        return None
    return (await _load(conn, [row]))[0]


async def _reload(conn: asyncpg.Connection, bid: UUID) -> Broadcast:
    broadcast = await get_broadcast(conn, str(bid))
    if broadcast is None:
        raise BroadcastError("Không tìm thấy phiên livestream.", status_code=404)
    return broadcast


async def promo_video_usage(conn: asyncpg.Connection, video_id: str) -> list[str]:
    """Titles of the broadcasts that play promo video ``video_id``."""
    rows = await conn.fetch(
        """
        SELECT DISTINCT b.title FROM broadcast_items i
          JOIN broadcasts b ON b.id = i.broadcast_id
         WHERE i.source = 'promo' AND i.source_ref = $1
         ORDER BY b.title
        """,
        video_id,
    )
    return [r["title"] for r in rows]


# ------------------------------------------------------------------- writing


async def _lock(conn: asyncpg.Connection, bid: UUID) -> asyncpg.Record:
    row = await conn.fetchrow(
        f"SELECT {_COLUMNS} FROM broadcasts WHERE id = $1 FOR UPDATE",  # noqa: S608
        bid,
    )
    if row is None:
        raise BroadcastError("Không tìm thấy phiên livestream.", status_code=404)
    return row


async def _timeline(conn: asyncpg.Connection, row: asyncpg.Record) -> Timeline:
    """The broadcast's playable timeline as stored right now (no product lookups)."""
    items = [_item(r, {}) for r in await _item_rows(conn, [row["id"]])]
    return build_timeline(
        next((i for i in items if i.role == "intro"), None),
        [i for i in items if i.role == "product"],
        next((i for i in items if i.role == "outro"), None),
        row["loop_mode"],
        row["loop_count"],
    )


async def _save_playback(
    conn: asyncpg.Connection,
    bid: UUID,
    status: Status,
    head: Playhead | None,
    *,
    started: bool = False,
) -> None:
    await conn.execute(
        """
        UPDATE broadcasts
           SET status = $2,
               playhead = $3::jsonb,
               started_at = CASE WHEN $4 THEN NOW() ELSE started_at END,
               ended_at = CASE WHEN $2 = 'ended' THEN COALESCE(ended_at, NOW())
                               WHEN $4 THEN NULL ELSE ended_at END,
               updated_at = NOW()
         WHERE id = $1
        """,
        bid,
        status,
        head.model_dump_json() if head is not None else None,
        started,
    )


@asynccontextmanager
async def _editing(conn: asyncpg.Connection, broadcast_id: str) -> AsyncIterator[asyncpg.Record]:
    """Lock the broadcast for a playlist edit and keep what is on air on air.

    The playhead is re-anchored to "now" against the timeline as it was
    before the edit, then stored with it, so the current video plays on from
    the same second wherever the edit moved it. If the edit removed it, the
    video that took its place starts from the beginning.
    """
    bid = _require_uuid(broadcast_id)
    async with conn.transaction():
        row = await _lock(conn, bid)
        head = _parse_playhead(row["playhead"]) if row["status"] in ON_AIR else None
        anchored = reanchor(await _timeline(conn, row), head, now_ms()) if head else None
        yield row
        if head is None:
            await conn.execute("UPDATE broadcasts SET updated_at = NOW() WHERE id = $1", bid)
        elif anchored is None:
            await _save_playback(conn, bid, "ended", None)
        else:
            await _save_playback(conn, bid, "live" if anchored.playing else "paused", anchored)
    hub.notify(str(bid))


async def create_broadcast(conn: asyncpg.Connection, fields: dict[str, Any]) -> Broadcast:
    bid = await conn.fetchval(
        """
        INSERT INTO broadcasts (title, notes, loop_mode, loop_count)
        VALUES ($1, $2, $3, $4) RETURNING id
        """,
        fields["title"],
        fields.get("notes") or "",
        fields.get("loop_mode") or "continuous",
        fields.get("loop_count") or 1,
    )
    return await _reload(conn, bid)


_SETTINGS = ("title", "notes", "loop_mode", "loop_count")


async def update_broadcast(
    conn: asyncpg.Connection, broadcast_id: str, fields: dict[str, Any]
) -> Broadcast:
    changes = {k: fields[k] for k in _SETTINGS if fields.get(k) is not None}
    async with _editing(conn, broadcast_id) as row:
        if changes:
            sets = ", ".join(f"{column} = ${n}" for n, column in enumerate(changes, start=2))
            await conn.execute(
                f"UPDATE broadcasts SET {sets} WHERE id = $1",  # noqa: S608 - whitelisted
                row["id"],
                *changes.values(),
            )
    return await _reload(conn, row["id"])


async def delete_broadcast(conn: asyncpg.Connection, broadcast_id: str) -> None:
    bid = _require_uuid(broadcast_id)
    async with conn.transaction():
        row = await _lock(conn, bid)
        if row["status"] in ON_AIR:
            raise BroadcastError("Phiên đang phát. Hãy dừng phiên trước khi xóa.")
        await conn.execute("DELETE FROM broadcasts WHERE id = $1", bid)
    hub.notify(str(bid))


async def _existing_products(conn: asyncpg.Connection, ids: set[str]) -> set[str]:
    uuids = [u for u in (to_uuid(i) for i in ids) if u is not None]
    if not uuids:
        return set()
    rows = await conn.fetch("SELECT id FROM products WHERE id = ANY($1::uuid[])", uuids)
    return {str(r["id"]) for r in rows}


def _source_facts(spec: ItemSpec) -> tuple[str | None, float | None]:
    """``(product_id, upload_duration_s)`` for a new item, after validating its source."""
    if spec.source == "promo":
        path = jobs.job_dir(data_dir(), spec.source_ref)
        job = jobs.load(path) if path is not None else None
        if job is None:
            raise BroadcastError("Không tìm thấy video.", status_code=404)
        return job.product.id, None
    info = media.upload_info(spec.source_ref)
    duration = info.get("duration_s") if info else None
    if not isinstance(duration, int | float) or duration <= 0:
        raise BroadcastError("Không tìm thấy tệp video đã tải lên.", status_code=404)
    return spec.product_id, float(duration)


async def add_items(
    conn: asyncpg.Connection, broadcast_id: str, specs: list[ItemSpec]
) -> Broadcast:
    """Append product videos; an intro or outro replaces the current one."""
    facts = [_source_facts(spec) for spec in specs]
    known = await _existing_products(conn, {pid for pid, _ in facts if pid})
    async with _editing(conn, broadcast_id) as row:
        bid = row["id"]
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM broadcast_items WHERE broadcast_id = $1 AND role = 'product'",
            bid,
        )
        added = sum(1 for spec in specs if spec.role == "product")
        if count + added > MAX_PRODUCT_ITEMS:
            raise BroadcastError(f"Một phiên tối đa {MAX_PRODUCT_ITEMS} video sản phẩm.")
        position = await conn.fetchval(
            "SELECT COALESCE(MAX(position) + 1, 0) FROM broadcast_items "
            "WHERE broadcast_id = $1 AND role = 'product'",
            bid,
        )
        for spec, (product_id, upload_s) in zip(specs, facts, strict=True):
            if spec.role != "product":
                await conn.execute(
                    "DELETE FROM broadcast_items WHERE broadcast_id = $1 AND role = $2",
                    bid,
                    spec.role,
                )
            await conn.execute(
                """
                INSERT INTO broadcast_items
                    (broadcast_id, role, position, source, source_ref, source_duration_s,
                     product_id, label, duration_s)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                bid,
                spec.role,
                position if spec.role == "product" else 0,
                spec.source,
                spec.source_ref,
                upload_s,
                to_uuid(product_id) if product_id in known else None,
                spec.label,
                spec.duration_s,
            )
            position += 1 if spec.role == "product" else 0
    return await _reload(conn, row["id"])


async def update_item(
    conn: asyncpg.Connection, broadcast_id: str, item_id: str, fields: dict[str, Any]
) -> Broadcast:
    """Change an item's time on air (``duration_s``, ``None`` for once) or label."""
    iid = _require_uuid(item_id, "video trong phiên")
    sets: list[str] = []
    args: list[Any] = []
    if "duration_s" in fields:
        args.append(fields["duration_s"])
        sets.append(f"duration_s = ${len(args) + 2}")
    if fields.get("label") is not None:
        args.append(fields["label"])
        sets.append(f"label = ${len(args) + 2}")
    async with _editing(conn, broadcast_id) as row:
        if sets:
            result = await conn.execute(
                f"UPDATE broadcast_items SET {', '.join(sets)}, updated_at = NOW() "  # noqa: S608
                "WHERE broadcast_id = $1 AND id = $2",
                row["id"],
                iid,
                *args,
            )
            if result.endswith(" 0"):
                raise BroadcastError("Không tìm thấy video trong phiên.", status_code=404)
    return await _reload(conn, row["id"])


async def remove_item(conn: asyncpg.Connection, broadcast_id: str, item_id: str) -> Broadcast:
    iid = _require_uuid(item_id, "video trong phiên")
    async with _editing(conn, broadcast_id) as row:
        result = await conn.execute(
            "DELETE FROM broadcast_items WHERE broadcast_id = $1 AND id = $2", row["id"], iid
        )
        if result.endswith(" 0"):
            raise BroadcastError("Không tìm thấy video trong phiên.", status_code=404)
    return await _reload(conn, row["id"])


async def reorder_items(
    conn: asyncpg.Connection, broadcast_id: str, item_ids: list[str]
) -> Broadcast:
    """Set the play order of the product videos; ``item_ids`` lists every one of them."""
    async with _editing(conn, broadcast_id) as row:
        rows = await conn.fetch(
            "SELECT id FROM broadcast_items WHERE broadcast_id = $1 AND role = 'product'",
            row["id"],
        )
        current = {str(r["id"]) for r in rows}
        if len(item_ids) != len(current) or set(item_ids) != current:
            raise BroadcastError(
                "Danh sách sắp xếp không khớp với các video trong phiên. Hãy tải lại trang.",
                status_code=422,
            )
        for position, item_id in enumerate(item_ids):
            await conn.execute(
                "UPDATE broadcast_items SET position = $3, updated_at = NOW() "
                "WHERE broadcast_id = $1 AND id = $2",
                row["id"],
                to_uuid(item_id),
                position,
            )
    return await _reload(conn, row["id"])


async def control(
    conn: asyncpg.Connection, broadcast_id: str, action: Action, item_id: str | None = None
) -> Broadcast:
    """Apply a control-room action (start, pause, next, ...) and return the result."""
    bid = _require_uuid(broadcast_id)
    async with conn.transaction():
        row = await _lock(conn, bid)
        timeline = await _timeline(conn, row)
        try:
            outcome = plan(
                action,
                timeline,
                row["status"],
                _parse_playhead(row["playhead"]),
                now_ms(),
                item_id,
            )
        except ControlError as exc:
            raise BroadcastError(str(exc)) from exc
        await _save_playback(conn, bid, outcome.status, outcome.playhead, started=outcome.started)
    hub.notify(str(bid))
    return await _reload(conn, bid)
