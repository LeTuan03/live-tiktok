"""Channel rotation orchestration for the SoShop agent.

The 24/7 channel cycles personas off the LLM's actual speech
completion (the ``SpeechHandle`` returned by ``generate_reply``),
rather than a wall-clock timer — so swaps land the moment each pitch
finishes, not 15 seconds in. ``ads.duration_ms`` is now a *safety
ceiling*: if the LLM/TTS hangs we force-rotate, but the typical case
is the pitch ending naturally inside ~15 seconds (the band the seed
scripts target). See ``server/seed/personas/README.md`` for the
word-count guidance.

The heavy lifting lives under ``soshop.agent.rotation`` — this
module keeps the DB helpers, the public ``run_channel_loop`` entry
point, and a few re-exports so existing callers
(``soshop.agent.main``, ``tests/agent/test_channel_loop.py``)
keep working unchanged.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import asyncpg
from livekit.agents import llm

from soshop.agent.loaders import (
    load_persona_voice_id as _db_load_persona_voice_id,
)
from soshop.agent.loaders import (
    load_popup_payload as _db_load_popup_payload,
)
from soshop.agent.rotation import (
    CHANNEL_LOOP_SWAP_SOURCE,
    ChannelLoopDeps,
    PitchOrchestrator,
    _ad_to_dispatch_metadata,
    build_default_completion,
    default_jitter,
)
from soshop.agent.runner import (
    AgentRunner,
    build_agent_session,
    build_agent_tools,
    build_pitch_tools,
    build_runtime_context,
    maybe_start_avatar,
    maybe_start_still_avatar,
)
from soshop.agent.schemas import DispatchMetadata
from soshop.agent.swap import _execute_swap_persona
from soshop.agent.tools import PRODUCT_PITCH_TOPIC
from soshop_server.core.config import settings
from soshop_server.db.models import Ad
from soshop_server.db.pool import get_pool

if TYPE_CHECKING:
    from livekit.agents import JobContext

logger = logging.getLogger("soshop.agent")


__all__ = [
    "CHANNEL_LOOP_SWAP_SOURCE",
    "ChannelLoopDeps",
    "_ad_to_dispatch_metadata",
    "_channel_loop_iteration",
    "_load_active_ads",
    "_record_channel_ad_ended",
    "_record_channel_ad_started",
    "_run_channel_iterations",
    "_write_channel_cursor",
    "run_channel_loop",
]


async def _load_active_ads(
    pool: asyncpg.Pool,
    *,
    persona_id: str | None = None,
) -> list[Ad]:
    """Read the current rotation from ``ads`` ordered by ``sequence_order``.

    Only ``status = 'active'`` rows participate. The query returns ``Ad``
    pydantic rows so the loop can use typed attribute access.

    When ``persona_id`` is supplied the result is scoped to ads for that
    seller — the model in which each channel is locked to one
    host and cycles only that host's pitches. Passing ``None`` keeps the
    legacy behavior (all active ads, multi-persona rotation) so older
    callers / tests don't break before they're migrated.
    """
    async with pool.acquire() as conn:
        if persona_id is None:
            rows = await conn.fetch(
                """
                SELECT id, advertiser_id, persona_id, product_id, script_id,
                       duration_ms, sequence_order, status, created_at, updated_at
                FROM ads
                WHERE status = 'active'
                ORDER BY sequence_order
                """
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, advertiser_id, persona_id, product_id, script_id,
                       duration_ms, sequence_order, status, created_at, updated_at
                FROM ads
                WHERE status = 'active' AND persona_id = $1
                ORDER BY sequence_order
                """,
                persona_id,
            )
    return [Ad.model_validate(dict(r)) for r in rows]


async def _load_channel_persona_id(pool: asyncpg.Pool, channel_id: str) -> str | None:
    """Return the persona bound to ``channel_id`` (or ``None`` if unbound).

    ``None`` is the legacy state — the channel was created before the
    channel-persona binding landed, or an operator manually NULLed the column.
    ``run_channel_loop`` falls back to the multi-persona rotation in
    that case so the channel still airs something rather than 404'ing.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT persona_id FROM channels WHERE id = $1",
            channel_id,
        )
    if row is None or row["persona_id"] is None:
        return None
    return str(row["persona_id"])


async def _write_channel_cursor(pool: asyncpg.Pool, channel_id: str, sequence_order: int) -> None:
    """Advance ``channels.current_ad_sequence`` so a reconciler / restart
    resumes from the same spot in the rotation.

    Also bumps ``last_heartbeat_at`` and forces ``status = 'running'`` so a
    channel that was marked ``starting`` flips over once the first ad airs.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE channels
               SET current_ad_sequence = $1,
                   last_heartbeat_at = NOW(),
                   status = 'running',
                   updated_at = NOW()
             WHERE id = $2
            """,
            sequence_order,
            channel_id,
        )


async def _record_channel_ad_started(pool: asyncpg.Pool, channel_id: str, ad: Ad) -> int:
    """Append a new ``channel_ad_log`` row and return its surrogate ``id``.

    The caller stashes the id and passes it to
    :func:`_record_channel_ad_ended` once the ad's window finishes so the
    log entry gets a non-null ``ended_at``.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO channel_ad_log (channel_id, ad_id)
            VALUES ($1, $2)
            RETURNING id
            """,
            channel_id,
            ad.id,
        )
    if row is None:  # pragma: no cover — INSERT ... RETURNING always returns
        raise RuntimeError("channel_ad_log insert returned no row")
    return int(row["id"])


async def _record_channel_ad_ended(pool: asyncpg.Pool, log_id: int) -> None:
    """Close out a ``channel_ad_log`` row with ``ended_at = NOW()``."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE channel_ad_log SET ended_at = NOW() WHERE id = $1",
            log_id,
        )


def _default_channel_loop_deps() -> ChannelLoopDeps:
    """Wire production callables for the rotation orchestrator.

    Used by :func:`run_channel_loop` and by tests that want the
    production behavior of every collaborator except the swap (which
    they still spy on).
    """

    async def _swap(runner: AgentRunner, md: DispatchMetadata, source: str) -> None:
        # ``_execute_swap_persona`` keeps ``source`` keyword-only; wrap
        # to match the positional signature ``ChannelLoopDeps`` exposes.
        await _execute_swap_persona(runner, md, source=source)

    async def _wait_for_pitch_complete_placeholder(**_: object) -> None:
        # The production wait is created per-session inside
        # ``run_channel_loop`` (it needs the live ``AgentSession``).
        # This placeholder is replaced before the orchestrator runs.
        raise RuntimeError(
            "wait_for_pitch_complete placeholder hit — orchestrator was not "
            "rewired with the per-session completion signal"
        )

    return ChannelLoopDeps(
        swap_persona=_swap,
        write_cursor=_write_channel_cursor,
        record_ad_started=_record_channel_ad_started,
        record_ad_ended=_record_channel_ad_ended,
        load_popup=_db_load_popup_payload,
        wait_for_pitch_complete=_wait_for_pitch_complete_placeholder,  # type: ignore[arg-type]
        jitter=default_jitter(),
    )


async def _channel_loop_iteration(
    runner: AgentRunner,
    pool: asyncpg.Pool,
    channel_id: str,
    ad: Ad,
    *,
    deps: ChannelLoopDeps,
) -> None:
    """Run one iteration of the channel rotation.

    Thin shim around :class:`PitchOrchestrator` that preserves the old
    callable signature so existing tests / callers don't break. Builds
    a one-shot orchestrator and runs a single iteration.
    """
    from soshop.agent.rotation.completion import (
        SpeechHandleCompletion,  # local import to avoid cycle at module load
    )

    orchestrator = PitchOrchestrator.build(
        runner=runner,
        pool=pool,
        ads=[ad],
        channel_id=channel_id,
        deps=deps,
        completion=SpeechHandleCompletion(runner.session),
        start_cursor=0,
    )
    await orchestrator._run_iteration(ad)  # noqa: SLF001 — single-iteration shim


async def _run_channel_iterations(
    runner: AgentRunner,
    pool: asyncpg.Pool,
    *,
    ads: list[Ad],
    start_cursor: int,
    channel_id: str,
    deps: ChannelLoopDeps,
    max_iterations: int | None = None,
) -> None:
    """Drive the rotation forever (or for a bounded number of iterations).

    Thin shim around :class:`PitchOrchestrator.run` for back-compat with
    existing call sites and tests. ``run_channel_loop`` constructs the
    orchestrator directly so it can wire the per-session completion
    signal; this shim falls back to :class:`SpeechHandleCompletion` and
    is suitable for tests that drive a fake session.
    """
    from soshop.agent.rotation.completion import SpeechHandleCompletion

    orchestrator = PitchOrchestrator.build(
        runner=runner,
        pool=pool,
        ads=ads,
        channel_id=channel_id,
        deps=deps,
        completion=SpeechHandleCompletion(runner.session),
        start_cursor=start_cursor,
    )
    await orchestrator.run(max_iterations=max_iterations)


async def run_channel_loop(ctx: JobContext, metadata: DispatchMetadata) -> None:
    """Always-on channel rotation: cycle through ``ads`` forever.

    The loop owns the session lifecycle directly rather than reusing the
    1:1 ``SessionLifecycle`` (which fires an auto-handoff after the cap).
    Channel agents never hand off — they swap personas in place until
    the worker process is told to leave.

    The avatar is started with ``LEMONSLICE_IDLE_TIMEOUT`` so an empty
    room between viewers doesn't reap the LemonSlice session. The
    persona for the *first* ad is wired into the initial AgentSession;
    subsequent ads are reached via ``_execute_swap_persona`` so the
    code path (interrupt, swap TTS, swap avatar image, swap prompt,
    rebind tools, generate_reply) is identical to a viewer swipe.

    The session is started with ``RoomInputOptions(close_on_disconnect=
    False)``. By default, livekit-agents closes the ``AgentSession`` as
    soon as the linked participant disconnects with reason
    ``CLIENT_INITIATED`` / ``ROOM_DELETED`` / ``USER_REJECTED`` (see the
    "closing agent session due to participant disconnect" log emitted
    by ``room_io``). For ad channels that's wrong: the broadcast must
    outlive any single viewer so the next client tuning in catches the
    rotation already in flight. With ``close_on_disconnect=False`` the
    session stays open across the constant viewer churn, the LemonSlice
    avatar (running with ``idle_timeout=-1``) keeps publishing, and the
    rotation keeps swapping ads until the worker itself is drained.

    Pacing is completion-driven: the orchestrator awaits the
    :class:`~soshop.agent.rotation.completion.PitchCompletionSignal`
    after each swap rather than sleeping on ``ad.duration_ms``. The
    duration field becomes a safety ceiling — see
    :class:`~soshop.agent.rotation.orchestrator.PitchOrchestrator`.
    """
    from livekit.agents.voice.room_io import RoomInputOptions

    from soshop.agent.control import install_agent_control_handler

    pool = await get_pool()
    channel_id = metadata.channel_id or ""
    channel_persona_id = await _load_channel_persona_id(pool, channel_id)
    ads = await _load_active_ads(pool, persona_id=channel_persona_id)
    if not ads:
        logger.error(
            "channel rotation has no active ads; bailing out without dispatching",
            extra={
                "channel_id": metadata.channel_id,
                "session_id": metadata.session_id,
                "channel_persona_id": channel_persona_id,
            },
        )
        ctx.shutdown(reason="channel rotation empty")
        return

    start_cursor = metadata.ad_start_index or 0
    initial_ad = ads[start_cursor % len(ads)]
    initial_popup = await _db_load_popup_payload(pool, str(initial_ad.product_id))
    initial_md = _ad_to_dispatch_metadata(metadata, initial_ad, popup_payload=initial_popup)
    runtime = await build_runtime_context(initial_md, pool=pool)
    voice_id, avatar_image_url = await _db_load_persona_voice_id(pool, initial_md.persona_id)

    logger.info(
        "channel agent runtime context loaded",
        extra={
            "channel_id": metadata.channel_id,
            "session_id": metadata.session_id,
            "ad_count": len(ads),
            "start_cursor": start_cursor,
            "initial_ad_sequence": initial_ad.sequence_order,
            "initial_persona_id": initial_md.persona_id,
            "initial_product_id": initial_md.product_id,
            "voice_id": voice_id,
            "avatar_configured": bool(avatar_image_url),
            "lemonslice_idle_timeout": settings.LEMONSLICE_IDLE_TIMEOUT,
            "pitch_topic": PRODUCT_PITCH_TOPIC,
        },
    )

    session = await build_agent_session(ctx, runtime, voice_id=voice_id)
    lemonslice_session_id = await maybe_start_avatar(
        ctx,
        session,
        avatar_image_url=avatar_image_url,
        idle_timeout=settings.LEMONSLICE_IDLE_TIMEOUT,
    )

    await ctx.connect()
    still_avatar = await maybe_start_still_avatar(ctx, session, avatar_image_url=avatar_image_url)

    from soshop.agent.pitch_agent import PitchAgent

    pitch_tools = build_pitch_tools(ctx, runtime)
    agent_tools: list[llm.Tool | llm.Toolset] = list(
        build_agent_tools(ctx, runtime, pitch_tools=pitch_tools)
    )
    agent = PitchAgent(instructions=runtime.persona_prompt, tools=agent_tools)

    runner = AgentRunner(
        ctx=ctx,
        session=session,
        agent=agent,
        pitch_tools=pitch_tools,
        runtime=runtime,
        lemonslice_session_id=lemonslice_session_id,
        still_avatar=still_avatar,
    )
    install_agent_control_handler(runner)

    await session.start(
        agent,
        room=ctx.room,
        room_input_options=RoomInputOptions(close_on_disconnect=False),
    )

    completion = build_default_completion(session)
    deps = _default_channel_loop_deps()
    # Bind the per-session completion signal as the production wait —
    # the placeholder in ``_default_channel_loop_deps`` only exists so
    # the dataclass can be constructed before we have a live session.
    deps.wait_for_pitch_complete = completion.wait_until_complete
    orchestrator = PitchOrchestrator.build(
        runner=runner,
        pool=pool,
        ads=ads,
        channel_id=metadata.channel_id or "",
        deps=deps,
        completion=completion,
        start_cursor=start_cursor,
    )
    orchestrator.min_slot_s = settings.CHANNEL_MIN_SLOT_SECONDS
    await orchestrator.run(max_iterations=None)
