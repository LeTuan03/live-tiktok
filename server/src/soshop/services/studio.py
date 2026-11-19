"""Data access for the live studio: AI models, live sessions, products.

Raw SQL through asyncpg, like the rest of the server. The route layer in
``soshop.api.routes.studio`` owns HTTP concerns; the agent worker's live host
(``soshop.agent.live_host``) reuses the same readers so both sides agree on
ordering and status semantics.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import asyncpg
from pydantic import BaseModel

Language = Literal["vi", "en"]
SessionStatus = Literal["draft", "live", "ended"]
ProductStatus = Literal["pending", "presenting", "done", "skipped"]
LiveMode = Literal["starting", "chatting", "presenting", "paused", "ending"]

# Persona rows created by the studio speak through the keyless Edge voices.
STUDIO_VOICE_PIPELINE_ID = "edge-tts"

# Vietnamese defaults to Google's native voice: it is the one keyless option
# that is both a native speaker and dependable. See `soshop.agent.edge_voice`
# for the measurements behind that.
DEFAULT_VOICE_BY_LANGUAGE: dict[str, str] = {
    "vi": "google-vi",
    "en": "en-US-AriaNeural",
}


class VoiceOption(BaseModel):
    id: str
    label: str
    language: Language
    gender: Literal["female", "male"]


VOICES: list[VoiceOption] = [
    VoiceOption(
        id="google-vi",
        label="Giọng Việt chuẩn (nữ, ổn định)",
        language="vi",
        gender="female",
    ),
    VoiceOption(
        id="vi-VN-HoaiMyNeural",
        label="Hoài My (nữ, tự nhiên hơn nhưng hay rớt)",
        language="vi",
        gender="female",
    ),
    VoiceOption(
        id="vi-VN-NamMinhNeural",
        label="Nam Minh (nam, hay rớt)",
        language="vi",
        gender="male",
    ),
    VoiceOption(
        id="en-US-AvaMultilingualNeural",
        label="Ava (nữ, giọng lai nước ngoài)",
        language="vi",
        gender="female",
    ),
    VoiceOption(
        id="en-US-BrianMultilingualNeural",
        label="Brian (nam, giọng lai nước ngoài)",
        language="vi",
        gender="male",
    ),
    VoiceOption(id="en-US-AriaNeural", label="Aria (female)", language="en", gender="female"),
    VoiceOption(id="en-US-JennyNeural", label="Jenny (female)", language="en", gender="female"),
    VoiceOption(id="en-US-GuyNeural", label="Guy (male)", language="en", gender="male"),
    VoiceOption(id="en-GB-SoniaNeural", label="Sonia, UK (female)", language="en", gender="female"),
]
_VOICE_IDS = {v.id for v in VOICES}


def resolve_voice(voice_id: str | None, language: str) -> str:
    """Pick the keyless voice for a model: its own if valid, else the language default."""
    if voice_id and voice_id in _VOICE_IDS:
        return voice_id
    return DEFAULT_VOICE_BY_LANGUAGE.get(language, DEFAULT_VOICE_BY_LANGUAGE["en"])


class ModelRow(BaseModel):
    """A studio AI model, backed by a ``personas`` row."""

    id: str
    name: str
    intro: str
    personality: str
    language: Language
    voice: str
    image_url: str | None
    created_at: datetime


class ProductRow(BaseModel):
    id: str
    session_id: str
    position: int
    name: str
    price: float | None
    currency: str
    description: str
    highlights: str
    image_url: str | None
    script: str
    status: ProductStatus


class LiveState(BaseModel):
    mode: LiveMode
    current_product_id: str | None = None
    last_line: str | None = None
    updated_at: datetime


class SessionRow(BaseModel):
    id: str
    title: str
    model_id: str | None
    notes: str
    status: SessionStatus
    room_name: str | None
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime
    live: LiveState | None


def to_uuid(value: str) -> UUID | None:
    try:
        return UUID(value)
    except (ValueError, TypeError, AttributeError):
        return None


# --------------------------------------------------------------------- models

_MODEL_COLUMNS = (
    "id, name, backstory, personality, language, voice_id, avatar_image_url, created_at"
)


def _model_from_record(row: asyncpg.Record) -> ModelRow:
    language = row["language"]
    return ModelRow(
        id=str(row["id"]),
        name=row["name"],
        intro=row["backstory"] or "",
        personality=row["personality"] or "",
        language=language,
        voice=resolve_voice(row["voice_id"], language),
        image_url=row["avatar_image_url"],
        created_at=row["created_at"],
    )


async def list_models(conn: asyncpg.Connection) -> list[ModelRow]:
    rows = await conn.fetch(
        f"SELECT {_MODEL_COLUMNS} FROM personas "  # noqa: S608 - constant column list
        "WHERE status = 'active' AND persona_kind = 'host' ORDER BY created_at"
    )
    return [_model_from_record(r) for r in rows]


async def get_model(conn: asyncpg.Connection, model_id: str) -> ModelRow | None:
    pid = to_uuid(model_id)
    if pid is None:
        return None
    row = await conn.fetchrow(
        f"SELECT {_MODEL_COLUMNS} FROM personas WHERE id = $1",  # noqa: S608
        pid,
    )
    return _model_from_record(row) if row else None


async def create_model(
    conn: asyncpg.Connection,
    *,
    name: str,
    intro: str,
    personality: str,
    language: str,
    voice: str,
    image_url: str,
) -> ModelRow:
    row = await conn.fetchrow(
        f"""
        INSERT INTO personas
            (name, avatar_image_url, voice_id, voice_pipeline_id, personality,
             backstory, persona_kind, status, language)
        VALUES ($1, $2, $3, $4, $5, $6, 'host', 'active', $7)
        RETURNING {_MODEL_COLUMNS}
        """,  # noqa: S608
        name,
        image_url,
        voice,
        STUDIO_VOICE_PIPELINE_ID,
        personality,
        intro,
        language,
    )
    assert row is not None  # noqa: S101 - INSERT ... RETURNING always returns
    return _model_from_record(row)


_MODEL_PATCH_COLUMNS = {
    "name": "name",
    "intro": "backstory",
    "personality": "personality",
    "language": "language",
    "voice": "voice_id",
}


async def update_model(
    conn: asyncpg.Connection, model_id: str, fields: dict[str, Any]
) -> ModelRow | None:
    pid = to_uuid(model_id)
    if pid is None:
        return None
    sets, args = _build_sets(fields, _MODEL_PATCH_COLUMNS)
    if sets:
        args.append(pid)
        await conn.execute(
            f"UPDATE personas SET {sets}, updated_at = NOW() WHERE id = ${len(args)}",  # noqa: S608
            *args,
        )
    return await get_model(conn, model_id)


async def delete_model(conn: asyncpg.Connection, model_id: str) -> bool:
    """Delete a model. Raises ``asyncpg.ForeignKeyViolationError`` when in use."""
    pid = to_uuid(model_id)
    if pid is None:
        return False
    result = await conn.execute("DELETE FROM personas WHERE id = $1", pid)
    return bool(result.endswith(" 1"))


async def model_in_live_session(conn: asyncpg.Connection, model_id: str) -> bool:
    pid = to_uuid(model_id)
    if pid is None:
        return False
    row = await conn.fetchrow(
        "SELECT 1 FROM live_sessions WHERE persona_id = $1 AND status = 'live' LIMIT 1", pid
    )
    return row is not None


# ------------------------------------------------------------------- sessions

_SESSION_COLUMNS = (
    "id, title, persona_id, notes, status, room_name, live_state, started_at, ended_at, created_at"
)


def _parse_live_state(raw: Any) -> LiveState | None:
    if raw is None:
        return None
    data = json.loads(raw) if isinstance(raw, str) else raw
    try:
        return LiveState.model_validate(data)
    except ValueError:
        return None


def _session_from_record(row: asyncpg.Record) -> SessionRow:
    status: SessionStatus = row["status"]
    return SessionRow(
        id=str(row["id"]),
        title=row["title"],
        model_id=str(row["persona_id"]) if row["persona_id"] else None,
        notes=row["notes"],
        status=status,
        room_name=row["room_name"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        created_at=row["created_at"],
        live=_parse_live_state(row["live_state"]) if status == "live" else None,
    )


async def list_sessions(conn: asyncpg.Connection) -> list[SessionRow]:
    rows = await conn.fetch(
        f"SELECT {_SESSION_COLUMNS} FROM live_sessions ORDER BY created_at DESC"  # noqa: S608
    )
    return [_session_from_record(r) for r in rows]


async def get_session(conn: asyncpg.Connection, session_id: str) -> SessionRow | None:
    sid = to_uuid(session_id)
    if sid is None:
        return None
    row = await conn.fetchrow(
        f"SELECT {_SESSION_COLUMNS} FROM live_sessions WHERE id = $1",  # noqa: S608
        sid,
    )
    return _session_from_record(row) if row else None


async def create_session(
    conn: asyncpg.Connection, *, title: str, model_id: str | None
) -> SessionRow:
    row = await conn.fetchrow(
        f"""
        INSERT INTO live_sessions (title, persona_id) VALUES ($1, $2)
        RETURNING {_SESSION_COLUMNS}
        """,  # noqa: S608
        title,
        to_uuid(model_id) if model_id else None,
    )
    assert row is not None  # noqa: S101
    return _session_from_record(row)


async def update_session(
    conn: asyncpg.Connection, session_id: str, fields: dict[str, Any]
) -> SessionRow | None:
    sid = to_uuid(session_id)
    if sid is None:
        return None
    values = dict(fields)
    if "model_id" in values:
        values["model_id"] = to_uuid(values["model_id"]) if values["model_id"] else None
    sets, args = _build_sets(values, {"title": "title", "notes": "notes", "model_id": "persona_id"})
    if sets:
        args.append(sid)
        await conn.execute(
            f"UPDATE live_sessions SET {sets}, updated_at = NOW() WHERE id = ${len(args)}",  # noqa: S608
            *args,
        )
    return await get_session(conn, session_id)


async def delete_session(conn: asyncpg.Connection, session_id: str) -> bool:
    sid = to_uuid(session_id)
    if sid is None:
        return False
    result = await conn.execute("DELETE FROM live_sessions WHERE id = $1", sid)
    return bool(result.endswith(" 1"))


async def mark_session_live(conn: asyncpg.Connection, session_id: str, room_name: str) -> None:
    """Flip a session on air and reset every product for a fresh run."""
    sid = to_uuid(session_id)
    state = LiveState(mode="starting", updated_at=datetime.now(UTC))
    async with conn.transaction():
        await conn.execute(
            """
            UPDATE live_sessions
               SET status = 'live', room_name = $2, live_state = $3::jsonb,
                   started_at = NOW(), ended_at = NULL, updated_at = NOW()
             WHERE id = $1
            """,
            sid,
            room_name,
            state.model_dump_json(),
        )
        await conn.execute(
            "UPDATE live_products SET status = 'pending', updated_at = NOW() WHERE session_id = $1",
            sid,
        )


async def mark_session_ended(conn: asyncpg.Connection, session_id: str) -> None:
    sid = to_uuid(session_id)
    async with conn.transaction():
        await conn.execute(
            """
            UPDATE live_sessions
               SET status = 'ended', live_state = NULL, ended_at = NOW(), updated_at = NOW()
             WHERE id = $1 AND status = 'live'
            """,
            sid,
        )
        await conn.execute(
            "UPDATE live_products SET status = 'pending', updated_at = NOW() "
            "WHERE session_id = $1 AND status = 'presenting'",
            sid,
        )


async def write_live_state(
    conn: asyncpg.Connection,
    session_id: str,
    *,
    mode: LiveMode,
    current_product_id: str | None,
    last_line: str | None,
) -> LiveState:
    """Agent-side state report. Only lands while the session is still live."""
    state = LiveState(
        mode=mode,
        current_product_id=current_product_id,
        last_line=last_line,
        updated_at=datetime.now(UTC),
    )
    await conn.execute(
        "UPDATE live_sessions SET live_state = $2::jsonb, updated_at = NOW() "
        "WHERE id = $1 AND status = 'live'",
        to_uuid(session_id),
        state.model_dump_json(),
    )
    return state


# ------------------------------------------------------------------- products

_PRODUCT_COLUMNS = (
    "id, session_id, position, name, price, currency, description, highlights, "
    "image_url, script, status"
)


def _product_from_record(row: asyncpg.Record) -> ProductRow:
    price: Decimal | None = row["price"]
    return ProductRow(
        id=str(row["id"]),
        session_id=str(row["session_id"]),
        position=row["position"],
        name=row["name"],
        price=float(price) if price is not None else None,
        currency=row["currency"],
        description=row["description"],
        highlights=row["highlights"],
        image_url=row["image_url"],
        script=row["script"],
        status=row["status"],
    )


async def list_products(conn: asyncpg.Connection, session_id: str) -> list[ProductRow]:
    sid = to_uuid(session_id)
    if sid is None:
        return []
    rows = await conn.fetch(
        f"SELECT {_PRODUCT_COLUMNS} FROM live_products "  # noqa: S608
        "WHERE session_id = $1 ORDER BY position, created_at",
        sid,
    )
    return [_product_from_record(r) for r in rows]


async def get_product(conn: asyncpg.Connection, product_id: str) -> ProductRow | None:
    pid = to_uuid(product_id)
    if pid is None:
        return None
    row = await conn.fetchrow(
        f"SELECT {_PRODUCT_COLUMNS} FROM live_products WHERE id = $1",  # noqa: S608
        pid,
    )
    return _product_from_record(row) if row else None


async def create_product(
    conn: asyncpg.Connection, session_id: str, fields: dict[str, Any]
) -> ProductRow:
    sid = to_uuid(session_id)
    row = await conn.fetchrow(
        f"""
        INSERT INTO live_products
            (session_id, position, name, price, currency, description,
             highlights, image_url, script)
        VALUES ($1,
                (SELECT COALESCE(MAX(position) + 1, 0) FROM live_products WHERE session_id = $1),
                $2, $3, $4, $5, $6, $7, $8)
        RETURNING {_PRODUCT_COLUMNS}
        """,  # noqa: S608
        sid,
        fields["name"],
        _to_decimal(fields.get("price")),
        fields.get("currency") or "VND",
        fields.get("description") or "",
        fields.get("highlights") or "",
        fields.get("image_url") or None,
        fields.get("script") or "",
    )
    assert row is not None  # noqa: S101
    return _product_from_record(row)


_PRODUCT_PATCH_COLUMNS = {
    "name": "name",
    "price": "price",
    "currency": "currency",
    "description": "description",
    "highlights": "highlights",
    "image_url": "image_url",
    "script": "script",
}


async def update_product(
    conn: asyncpg.Connection, product_id: str, fields: dict[str, Any]
) -> ProductRow | None:
    pid = to_uuid(product_id)
    if pid is None:
        return None
    values = dict(fields)
    if "price" in values:
        values["price"] = _to_decimal(values["price"])
    sets, args = _build_sets(values, _PRODUCT_PATCH_COLUMNS)
    if sets:
        args.append(pid)
        await conn.execute(
            f"UPDATE live_products SET {sets}, updated_at = NOW() WHERE id = ${len(args)}",  # noqa: S608
            *args,
        )
    return await get_product(conn, product_id)


async def delete_product(conn: asyncpg.Connection, product_id: str) -> bool:
    pid = to_uuid(product_id)
    if pid is None:
        return False
    result = await conn.execute("DELETE FROM live_products WHERE id = $1", pid)
    return bool(result.endswith(" 1"))


async def reorder_products(
    conn: asyncpg.Connection, session_id: str, product_ids: list[str]
) -> list[ProductRow] | None:
    """Rewrite positions to match ``product_ids``.

    Returns ``None`` when the list is not exactly the session's product set,
    so a stale client cannot silently drop or duplicate a product.
    """
    current = await list_products(conn, session_id)
    if sorted(p.id for p in current) != sorted(product_ids):
        return None
    async with conn.transaction():
        for position, product_id in enumerate(product_ids):
            await conn.execute(
                "UPDATE live_products SET position = $2, updated_at = NOW() WHERE id = $1",
                to_uuid(product_id),
                position,
            )
    return await list_products(conn, session_id)


async def set_product_status(
    conn: asyncpg.Connection, product_id: str, status: ProductStatus
) -> None:
    await conn.execute(
        "UPDATE live_products SET status = $2, updated_at = NOW() WHERE id = $1",
        to_uuid(product_id),
        status,
    )


async def reset_product_statuses(conn: asyncpg.Connection, session_id: str) -> None:
    await conn.execute(
        "UPDATE live_products SET status = 'pending', updated_at = NOW() WHERE session_id = $1",
        to_uuid(session_id),
    )


# -------------------------------------------------------------------- helpers


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _build_sets(fields: dict[str, Any], columns: dict[str, str]) -> tuple[str, list[Any]]:
    """Build a ``col = $n`` list for the keys of ``fields`` that are patchable."""
    parts: list[str] = []
    args: list[Any] = []
    for key, column in columns.items():
        if key not in fields:
            continue
        args.append(fields[key])
        parts.append(f"{column} = ${len(args)}")
    return ", ".join(parts), args
