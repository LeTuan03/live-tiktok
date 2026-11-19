"""Service layer for the public ``/customize`` ("create your AI host") page.

Three responsibilities, kept out of the route module so they stay unit-
testable without spinning up FastAPI:

1. :func:`load_curated_voices` — serve a fixed, committed list of host voices
   read from ``seed/curated_voices.json`` (no live ElevenLabs proxy).
2. :func:`validate_script` / :func:`insert_draft_host` — validate the user's
   script against the spoken-word band and persist an *ephemeral* draft
   persona + its single script.
3. :func:`dispatch_custom_host` — dispatch the LiveKit agent for that draft
   and mint a join token, reusing the same helpers the feed uses.

Everything here reuses existing primitives; no new tables or migrations.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import asyncpg
import httpx
from pydantic import BaseModel

from soshop.agent.pitch_timing import WORD_COUNT_MAX, WORD_COUNT_MIN, count_words
from soshop.agent.schemas import DispatchMetadata
from soshop.api.routes._feed_registry import PLUG_AGENT_NAME
from soshop.livekit.dispatch import create_validated_dispatch, mint_room_access_token
from soshop_server.core.config import settings

if TYPE_CHECKING:
    from livekit.api import LiveKitAPI

logger = logging.getLogger("soshop.services.customize")

# Personas created here speak through the same ElevenLabs pipeline the seed
# hosts use — mirror the model pinned in ``soshop.agent.runner`` so the
# NOT NULL ``personas.voice_pipeline_id`` column is populated consistently.
VOICE_PIPELINE_ID = "elevenlabs-eleven_turbo_v2_5"

_HTTP_TIMEOUT_S = 10.0

# The curated host voices shown by the ``/customize`` picker. A committed seed
# file (instead of a live Voice Library search) so the picker is a small, fixed
# list we control; the source ids are preloaded into our account by
# ``scripts/preload_curated_voices.py`` so each resolves to a stable, owned
# voice at dispatch. Resolve up from this module (services -> soshop -> src ->
# server) to ``server/seed/curated_voices.json``.
_CURATED_VOICES_PATH = Path(__file__).resolve().parents[3] / "seed" / "curated_voices.json"

# Ephemeral draft personas are cleaned up opportunistically (see
# :func:`cleanup_stale_drafts`) rather than via a scheduler; the 1-day TTL is a
# literal in the DELETE query.


# ---------------------------------------------------------------------------
# Curated host voices (committed seed)
# ---------------------------------------------------------------------------


class VoiceOut(BaseModel):
    """A single Voice Library voice surfaced to the customize voice picker.

    ``voice_id`` feeds straight into the avatar's TTS pipeline; the rest are
    display/filter metadata from the library entry. ``public_owner_id`` is the
    voice's owner in the public library; the client echoes it back on submit so
    the server can copy the voice into our account (see
    :func:`ensure_voice_in_account`) and guarantee it stays usable at dispatch.
    """

    voice_id: str
    public_owner_id: str | None = None
    name: str
    preview_url: str | None = None
    category: str | None = None
    gender: str | None = None
    age: str | None = None
    accent: str | None = None
    language: str | None = None
    use_case: str | None = None
    descriptive: str | None = None
    image_url: str | None = None


class VoicesUnavailableError(RuntimeError):
    """Raised when the voice catalogue can't be listed (no key / API error)."""


# Parsed once and cached for the process lifetime: the seed file is committed
# and immutable at runtime, so a single read is enough.
_curated_voices_cache: list[VoiceOut] | None = None


def load_curated_voices() -> list[VoiceOut]:
    """Return the curated host voices from the committed seed, in file order.

    Reads ``seed/curated_voices.json`` once and caches the parsed result at
    module level. Static and local, so it never touches ElevenLabs and never
    raises :class:`VoicesUnavailableError`.
    """
    global _curated_voices_cache
    if _curated_voices_cache is None:
        raw = json.loads(_CURATED_VOICES_PATH.read_text(encoding="utf-8"))
        _curated_voices_cache = [
            VoiceOut(
                voice_id=entry["voice_id"],
                public_owner_id=entry["public_owner_id"],
                name=entry["display_name"],
                preview_url=entry["preview_url"],
                gender=entry["gender"],
                age=entry.get("age"),
                accent=entry.get("accent"),
                descriptive=entry["vibe"],
                language="en",
                category=None,
                use_case=None,
                image_url=None,
            )
            for entry in raw["voices"]
        ]
    return _curated_voices_cache


# ---------------------------------------------------------------------------
# Guaranteeing a picked voice is usable at dispatch
# ---------------------------------------------------------------------------

# A Voice Library voice used directly by id is only as durable as the original
# owner's sharing toggle: if they unshare or delete it, the id starts returning
# `voice_id_does_not_exist` and the host falls silent (the agent's TTS fallback
# then drops to the default voice). Adding the voice to our account via
# ElevenLabs' "add shared voice" endpoint makes it durable. The ``voice_id`` is
# unchanged (the add returns the same id), but the voice now lives in our
# library, so it survives the original owner's changes and `GET /v1/voices/{id}`
# resolves it. Premade voices (no ``public_owner_id``) are already account-
# agnostic and need no add.
_ELEVEN_VOICE_URL = "https://api.elevenlabs.io/v1/voices/{voice_id}"
_ELEVEN_ADD_VOICE_URL = "https://api.elevenlabs.io/v1/voices/add/{public_owner_id}/{voice_id}"


async def _voice_in_account(voice_id: str) -> bool:
    """Whether ``voice_id`` is already resident in our account library.

    ``GET /v1/voices/{id}`` returns 200 only for voices in *our* account; a
    shared voice not yet added returns 400/404. Any other status is treated as
    unavailable so the caller degrades rather than guesses.
    """
    if not settings.ELEVEN_API_KEY:
        raise VoicesUnavailableError("ELEVEN_API_KEY is not configured")
    headers = {"xi-api-key": settings.ELEVEN_API_KEY}
    url = _ELEVEN_VOICE_URL.format(voice_id=voice_id)
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as http:
            resp = await http.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise VoicesUnavailableError(f"ElevenLabs voice lookup failed: {exc}") from exc
    if resp.status_code == 200:
        return True
    if resp.status_code in (400, 404):
        return False
    raise VoicesUnavailableError(f"ElevenLabs voice lookup returned {resp.status_code}")


async def _add_shared_voice(public_owner_id: str, voice_id: str, new_name: str) -> None:
    """Add a Voice Library voice to our account (its ``voice_id`` is unchanged).

    ``POST /v1/voices/add/{public_user_id}/{voice_id}`` with a ``new_name``
    body just makes the voice resident in our library. Raises on HTTP failure.
    """
    if not settings.ELEVEN_API_KEY:
        raise VoicesUnavailableError("ELEVEN_API_KEY is not configured")
    headers = {"xi-api-key": settings.ELEVEN_API_KEY}
    url = _ELEVEN_ADD_VOICE_URL.format(public_owner_id=public_owner_id, voice_id=voice_id)
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as http:
            resp = await http.post(url, headers=headers, json={"new_name": new_name})
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise VoicesUnavailableError(f"ElevenLabs add-voice request failed: {exc}") from exc


async def ensure_voice_in_account(
    voice_id: str, public_owner_id: str | None, *, name: str | None = None
) -> str:
    """Make a picked Voice Library voice durable; return its (unchanged) id.

    For a Voice Library voice (has ``public_owner_id``) we add it to our account
    when it isn't already there, so its availability no longer depends on the
    original owner. The ``voice_id`` is returned unchanged either way (adding a
    shared voice keeps its id); a premade voice (no ``public_owner_id``) is a
    no-op.

    Best-effort: on any ElevenLabs failure (outage, voice-count cap, the source
    voice already gone) we return the id without raising rather than block host
    creation. The agent's TTS fallback (see :mod:`soshop.agent.pitch_agent`)
    keeps the host audible, so a failure degrades instead of hard-failing.
    """
    if not public_owner_id:
        return voice_id
    try:
        if not await _voice_in_account(voice_id):
            await _add_shared_voice(public_owner_id, voice_id, name or voice_id)
            logger.info("customize_voice_added_to_library", extra={"voice_id": voice_id})
    except VoicesUnavailableError:
        logger.warning(
            "customize_voice_add_failed; dispatching with the id as-is + runtime fallback",
            extra={"voice_id": voice_id},
            exc_info=True,
        )
    return voice_id


# ---------------------------------------------------------------------------
# Script validation + draft-persona persistence
# ---------------------------------------------------------------------------


class ScriptOutOfBandError(ValueError):
    """Raised when the user's script falls outside the spoken-word band."""

    def __init__(self, count: int) -> None:
        self.count = count
        super().__init__(
            f"Script is {count} words; it must be {WORD_COUNT_MIN}-{WORD_COUNT_MAX} "
            "words (about 12-18 seconds of speech)."
        )


def validate_script(script: str) -> int:
    """Return the script's word count, raising if it's outside the band."""
    count = count_words(script)
    if not WORD_COUNT_MIN <= count <= WORD_COUNT_MAX:
        raise ScriptOutOfBandError(count)
    return count


async def cleanup_stale_drafts(conn: asyncpg.Connection) -> None:
    """Best-effort delete of draft personas older than the TTL.

    ``persona_scripts`` rows cascade on persona delete. Runs opportunistically
    at create time so an ephemeral demo needs no scheduler. Never raises — a
    cleanup failure must not break a create.
    """
    try:
        # Literal interval (a hardcoded constant, no injection surface): a
        # bound timedelta param trips type inference on the pgbouncer pooler
        # ("operator does not exist: timestamptz < interval").
        await conn.execute(
            "DELETE FROM personas WHERE status = 'draft' AND created_at < NOW() - INTERVAL '1 day'"
        )
    except Exception:  # noqa: BLE001 - cleanup is best-effort
        logger.warning("cleanup_stale_drafts failed; continuing", exc_info=True)


@dataclass(frozen=True)
class DraftHost:
    persona_id: str
    script_id: str


async def insert_draft_host(
    conn: asyncpg.Connection,
    *,
    name: str,
    voice_id: str,
    avatar_image_url: str,
    personality: str,
    script_text: str,
) -> DraftHost:
    """Insert an ephemeral ``status='draft'`` persona + its single script.

    Both rows go in one transaction. The agent loads everything it needs by
    id (see ``soshop.agent.loaders``), so no further wiring is required.
    """
    async with conn.transaction():
        persona_row = await conn.fetchrow(
            """
            INSERT INTO personas
                (name, avatar_image_url, voice_id, voice_pipeline_id,
                 personality, persona_kind, status)
            VALUES ($1, $2, $3, $4, $5, 'host', 'draft')
            RETURNING id
            """,
            name,
            avatar_image_url,
            voice_id,
            VOICE_PIPELINE_ID,
            personality,
        )
        persona_id = persona_row["id"]
        script_row = await conn.fetchrow(
            """
            INSERT INTO persona_scripts (persona_id, script_text, weight)
            VALUES ($1, $2, 1)
            RETURNING id
            """,
            persona_id,
            script_text,
        )
        script_id = script_row["id"]
    return DraftHost(persona_id=str(persona_id), script_id=str(script_id))


# ---------------------------------------------------------------------------
# Dispatch + token
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DispatchedRoom:
    room_name: str
    livekit_url: str
    token: str
    session_id: str


async def dispatch_custom_host(
    api_client: LiveKitAPI,
    *,
    persona_id: str,
    script_id: str,
    user_id: str,
) -> DispatchedRoom:
    """Dispatch the agent for a draft persona and mint a viewer join token.

    Mirrors ``feed._dispatch_first_persona`` but without the feed registry /
    cost gate: the customize preview is a self-contained 1:1 room, and the
    browser connects with the returned ``token`` directly.
    """
    session_id = uuid4().hex
    room_name = f"customize-{uuid4().hex}"
    metadata = DispatchMetadata(
        persona_id=persona_id,
        product_id=None,
        script_id=script_id,
        pitch_window_ms=0,
        session_id=session_id,
        user_id=user_id,
        # Hold the single one-shot pitch until the browser has unlocked audio
        # playback and signals readiness; otherwise it plays during the avatar
        # warmup while the client is still muted and the viewer never hears it.
        await_viewer_ready=True,
    )
    await create_validated_dispatch(
        api_client,
        agent_name=PLUG_AGENT_NAME,
        room=room_name,
        metadata=metadata,
    )
    token = mint_room_access_token(
        api_key=settings.LIVEKIT_API_KEY,
        api_secret=settings.LIVEKIT_API_SECRET,
        identity=user_id,
        room=room_name,
        metadata=metadata,
    )
    return DispatchedRoom(
        room_name=room_name,
        livekit_url=settings.LIVEKIT_URL,
        token=token,
        session_id=session_id,
    )
