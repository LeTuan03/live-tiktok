"""Stream destinations and broadcast outputs (migration 0007).

A destination is an RTMP ingest point configured in the studio (the server
URL and stream key TikTok LIVE Producer or Shopee Seller Centre hands out). An
output switches a destination on for one broadcast and carries the push state
the restreamer writes back (:mod:`soshop.restream.runner`).

Stream keys go from here to the restreamer only. The shapes the API returns
carry a masked hint (:func:`soshop.restream.platforms.mask_key`), never a key.

A destination marked ``auto_on`` (in the studio, or ``TIKTOK_AUTO_ON`` for the
one the settings describe, :func:`sync_config_destination`) is handed to each
broadcast as it is started (:func:`take_over`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import asyncpg
from pydantic import BaseModel

from soshop.restream.platforms import (
    LABELS,
    DestinationError,
    OutputState,
    OutputStatus,
    Platform,
    ingest_url,
    mask_key,
    needs_key,
    normalize_key,
    normalize_server_url,
)
from soshop.services.studio import to_uuid


class RestreamError(Exception):
    """A request about destinations or outputs that cannot be honoured."""

    def __init__(self, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class Destination(BaseModel):
    id: str
    platform: Platform
    platform_label: str
    name: str
    server_url: str
    has_key: bool
    key_hint: str
    """The key's last characters, for telling keys apart. Never the key."""
    auto_on: bool
    """Switched on by itself for the livestream session started last."""
    created_at: datetime
    updated_at: datetime


class Output(BaseModel):
    """One destination as seen from one broadcast."""

    destination: Destination
    enabled: bool
    state: OutputState
    detail: str
    state_at: datetime | None


@dataclass(frozen=True)
class Wanted:
    """An output that should be pushing now, with what the restreamer needs."""

    broadcast_id: str
    destination_id: str
    url: str | None
    """The full ingest URL, ``None`` when the destination cannot be used."""
    secrets: tuple[str, ...]
    problem: str | None


_DEST_COLUMNS = "id, platform, name, server_url, stream_key, auto_on, created_at, updated_at"
_NOT_FOUND = "Không tìm thấy kênh phát."


def _uuid(value: str, message: str = _NOT_FOUND) -> Any:
    uid = to_uuid(value)
    if uid is None:
        raise RestreamError(message, status_code=404)
    return uid


def _destination(row: asyncpg.Record) -> Destination:
    key = row["stream_key"] or ""
    return Destination(
        id=str(row["id"]),
        platform=row["platform"],
        platform_label=LABELS.get(row["platform"], row["platform"]),
        name=row["name"],
        server_url=row["server_url"],
        has_key=bool(key),
        key_hint=mask_key(key),
        auto_on=row["auto_on"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _checked(fields: dict[str, Any]) -> dict[str, Any]:
    """``fields`` with the URL and key normalised; a bad one is a 422."""
    out = dict(fields)
    try:
        if out.get("server_url") is not None:
            out["server_url"] = normalize_server_url(out["server_url"])
        if out.get("stream_key") is not None:
            out["stream_key"] = normalize_key(out["stream_key"])
    except DestinationError as exc:
        raise RestreamError(str(exc), status_code=422) from exc
    if out.get("name") is not None:
        out["name"] = out["name"].strip()
        if not out["name"]:
            raise RestreamError("Hãy đặt tên cho kênh phát.", status_code=422)
    return out


# -------------------------------------------------------------- destinations


async def list_destinations(conn: asyncpg.Connection) -> list[Destination]:
    rows = await conn.fetch(
        f"SELECT {_DEST_COLUMNS} FROM stream_destinations ORDER BY created_at"  # noqa: S608
    )
    return [_destination(r) for r in rows]


async def create_destination(conn: asyncpg.Connection, fields: dict[str, Any]) -> Destination:
    values = _checked(fields)
    row = await conn.fetchrow(
        f"""
        INSERT INTO stream_destinations (platform, name, server_url, stream_key, auto_on)
        VALUES ($1, $2, $3, $4, $5) RETURNING {_DEST_COLUMNS}
        """,  # noqa: S608 - constant columns
        values["platform"],
        values["name"],
        values["server_url"],
        values.get("stream_key") or "",
        bool(values.get("auto_on", False)),
    )
    return _destination(row)


_EDITABLE = ("name", "server_url", "stream_key", "auto_on")


async def update_destination(
    conn: asyncpg.Connection, destination_id: str, fields: dict[str, Any]
) -> Destination:
    """Change a destination. A field left out, or a ``None`` key, keeps its value."""
    did = _uuid(destination_id)
    changes = {k: v for k, v in _checked(fields).items() if k in _EDITABLE and v is not None}
    sets = [f"{column} = ${n}" for n, column in enumerate(changes, start=2)]
    sets.append("updated_at = NOW()")
    row = await conn.fetchrow(
        f"UPDATE stream_destinations SET {', '.join(sets)} WHERE id = $1 "  # noqa: S608
        f"RETURNING {_DEST_COLUMNS}",
        did,
        *changes.values(),
    )
    if row is None:
        raise RestreamError(_NOT_FOUND, status_code=404)
    return _destination(row)


CONFIG_TIKTOK_ID = str(uuid5(NAMESPACE_URL, "soshop:stream-destination:config-tiktok"))
"""The TikTok destination ``TIKTOK_SERVER_URL`` / ``TIKTOK_STREAM_KEY`` describe.

A fixed id, so every startup updates the same row instead of adding one.
"""
CONFIG_TIKTOK_NAME = "TikTok Live (cấu hình)"


async def sync_config_destination(
    conn: asyncpg.Connection,
    *,
    server_url: str,
    stream_key: str,
    auto_on: bool = True,
    destination_id: str = CONFIG_TIKTOK_ID,
    overwrite: bool = True,
) -> str:
    """Create or refresh the TikTok destination the settings describe; its id.

    With ``overwrite`` the settings win over a URL or key edited in the
    studio; without it an existing row is left as it is (only a missing one
    is created). The name is only set on creation, so a rename sticks. A URL
    or key that cannot be used raises :class:`RestreamError`.
    """
    values = _checked({"server_url": server_url, "stream_key": stream_key})
    on_conflict = (
        """
        DO UPDATE
           SET server_url = EXCLUDED.server_url,
               stream_key = EXCLUDED.stream_key,
               auto_on = EXCLUDED.auto_on,
               updated_at = NOW()
         WHERE stream_destinations.server_url <> EXCLUDED.server_url
            OR stream_destinations.stream_key <> EXCLUDED.stream_key
            OR stream_destinations.auto_on <> EXCLUDED.auto_on
        """
        if overwrite
        else "DO NOTHING"
    )
    await conn.execute(
        f"""
        INSERT INTO stream_destinations (id, platform, name, server_url, stream_key, auto_on)
        VALUES ($1, 'tiktok', $2, $3, $4, $5)
        ON CONFLICT (id) {on_conflict}
        """,  # noqa: S608 - two constant clauses
        _uuid(destination_id),
        CONFIG_TIKTOK_NAME,
        values["server_url"],
        values["stream_key"],
        auto_on,
    )
    return destination_id


Start = tuple[str, datetime]
"""A broadcast and the moment Start was last pressed on it (``started_at``)."""


async def latest_start(conn: asyncpg.Connection) -> Start | None:
    """The broadcast on air that was started last."""
    row = await conn.fetchrow(
        """
        SELECT id, started_at FROM broadcasts
         WHERE status IN ('live', 'paused') AND started_at IS NOT NULL
         ORDER BY started_at DESC, id
         LIMIT 1
        """
    )
    return (str(row["id"]), row["started_at"]) if row is not None else None


async def switched_starts(conn: asyncpg.Connection) -> set[Start]:
    """Broadcasts on air that already have a switch, on or off, for an auto destination."""
    rows = await conn.fetch(
        """
        SELECT DISTINCT b.id, b.started_at
          FROM broadcast_outputs o
          JOIN broadcasts b ON b.id = o.broadcast_id
          JOIN stream_destinations d ON d.id = o.destination_id
         WHERE d.auto_on AND b.status IN ('live', 'paused') AND b.started_at IS NOT NULL
        """
    )
    return {(str(r["id"]), r["started_at"]) for r in rows}


async def auto_destinations(conn: asyncpg.Connection) -> list[str]:
    """Destinations switched on by themselves for each session as Start is pressed."""
    rows = await conn.fetch("SELECT id FROM stream_destinations WHERE auto_on ORDER BY created_at")
    return [str(r["id"]) for r in rows]


async def take_over(conn: asyncpg.Connection, broadcast_id: str, destination_id: str) -> bool:
    """Give ``destination_id`` to one broadcast on air; whether it took.

    Switches it on for ``broadcast_id`` and off for every other broadcast on
    air, since one stream key carries one stream. Does nothing when the
    broadcast is no longer on air or the destination was deleted meanwhile.
    """
    bid, did = _uuid(broadcast_id), _uuid(destination_id)
    async with conn.transaction():
        took = await conn.fetchval(
            """
            INSERT INTO broadcast_outputs (broadcast_id, destination_id, enabled)
            SELECT b.id, d.id, TRUE
              FROM broadcasts b, stream_destinations d
             WHERE b.id = $1 AND d.id = $2 AND b.status IN ('live', 'paused')
            ON CONFLICT (broadcast_id, destination_id) DO UPDATE SET enabled = TRUE
            RETURNING TRUE
            """,
            bid,
            did,
        )
        if not took:
            return False
        await conn.execute(
            """
            UPDATE broadcast_outputs o SET enabled = FALSE
              FROM broadcasts b
             WHERE b.id = o.broadcast_id AND o.destination_id = $2 AND o.broadcast_id <> $1
               AND o.enabled AND b.status IN ('live', 'paused')
            """,
            bid,
            did,
        )
    return True


async def delete_destination(conn: asyncpg.Connection, destination_id: str) -> None:
    did = _uuid(destination_id)
    async with conn.transaction():
        busy = await conn.fetchval(
            """
            SELECT b.title FROM broadcast_outputs o
              JOIN broadcasts b ON b.id = o.broadcast_id
             WHERE o.destination_id = $1 AND o.enabled AND b.status IN ('live', 'paused')
             LIMIT 1
            """,
            did,
        )
        if busy is not None:
            raise RestreamError(
                f'Kênh đang phát trong phiên "{busy}". Tắt kênh ở phiên đó trước khi xóa.'
            )
        result = await conn.execute("DELETE FROM stream_destinations WHERE id = $1", did)
    if result.endswith(" 0"):
        raise RestreamError(_NOT_FOUND, status_code=404)


# ------------------------------------------------------------------- outputs


async def list_outputs(conn: asyncpg.Connection, broadcast_id: str) -> list[Output]:
    """Every destination, switched on or off for broadcast ``broadcast_id``."""
    bid = _uuid(broadcast_id, "Không tìm thấy phiên livestream.")
    if await conn.fetchval("SELECT 1 FROM broadcasts WHERE id = $1", bid) is None:
        raise RestreamError("Không tìm thấy phiên livestream.", status_code=404)
    rows = await conn.fetch(
        """
        SELECT d.id, d.platform, d.name, d.server_url, d.stream_key, d.auto_on, d.created_at,
               d.updated_at, o.enabled, o.state, o.detail, o.state_at
          FROM stream_destinations d
          LEFT JOIN broadcast_outputs o ON o.destination_id = d.id AND o.broadcast_id = $1
         ORDER BY d.created_at
        """,
        bid,
    )
    return [
        Output(
            destination=_destination(r),
            enabled=bool(r["enabled"]),
            state=r["state"] or "off",
            detail=r["detail"] or "",
            state_at=r["state_at"],
        )
        for r in rows
    ]


async def set_output(
    conn: asyncpg.Connection, broadcast_id: str, destination_id: str, enabled: bool
) -> list[Output]:
    """Switch ``destination_id`` on or off for the broadcast; returns every output."""
    bid = _uuid(broadcast_id, "Không tìm thấy phiên livestream.")
    did = _uuid(destination_id)
    if await conn.fetchval("SELECT 1 FROM broadcasts WHERE id = $1", bid) is None:
        raise RestreamError("Không tìm thấy phiên livestream.", status_code=404)
    if await conn.fetchval("SELECT 1 FROM stream_destinations WHERE id = $1", did) is None:
        raise RestreamError(_NOT_FOUND, status_code=404)
    await conn.execute(
        """
        INSERT INTO broadcast_outputs (broadcast_id, destination_id, enabled)
        VALUES ($1, $2, $3)
        ON CONFLICT (broadcast_id, destination_id) DO UPDATE SET enabled = EXCLUDED.enabled
        """,
        bid,
        did,
        enabled,
    )
    return await list_outputs(conn, broadcast_id)


# ------------------------------------------------------------ the restreamer


def _wanted(row: asyncpg.Record) -> Wanted:
    key = row["stream_key"] or ""
    bid, did = str(row["broadcast_id"]), str(row["destination_id"])
    if needs_key(row["platform"]) and not key:
        problem = "Chưa có stream key. Lấy key của phiên live rồi dán vào trang Kênh phát."
        return Wanted(bid, did, None, (), problem)
    try:
        server = normalize_server_url(row["server_url"])
    except DestinationError as exc:
        return Wanted(bid, did, None, (), str(exc))
    return Wanted(bid, did, ingest_url(server, key), (key,) if key else (), None)


async def wanted_outputs(conn: asyncpg.Connection) -> list[Wanted]:
    """Outputs switched on for a broadcast that is live or paused."""
    rows = await conn.fetch(
        """
        SELECT o.broadcast_id, o.destination_id, d.platform, d.server_url, d.stream_key
          FROM broadcast_outputs o
          JOIN stream_destinations d ON d.id = o.destination_id
          JOIN broadcasts b ON b.id = o.broadcast_id
         WHERE o.enabled AND b.status IN ('live', 'paused')
         ORDER BY o.broadcast_id, d.created_at
        """
    )
    return [_wanted(r) for r in rows]


async def save_status(
    conn: asyncpg.Connection, broadcast_id: str, destination_id: str, status: OutputStatus
) -> None:
    await conn.execute(
        """
        UPDATE broadcast_outputs SET state = $3, detail = $4, state_at = NOW()
         WHERE broadcast_id = $1 AND destination_id = $2
        """,
        to_uuid(broadcast_id),
        to_uuid(destination_id),
        status.state,
        status.detail[:500],
    )


async def reset_states(conn: asyncpg.Connection) -> None:
    """Nothing is pushing yet: clear what a previous process left behind."""
    await conn.execute(
        "UPDATE broadcast_outputs SET state = 'off', detail = '', state_at = NOW() "
        "WHERE state <> 'off'"
    )
