# server/: SoShop API + LiveKit agent

FastAPI backend plus a LiveKit Agents worker. Python 3.11, managed by **uv**, asyncpg against Postgres, an S3-compatible bucket (Tigris in prod, MinIO locally) for assets.

The app runs open access: there is no external auth provider. User-scoped routes resolve identity from an optional `X-User-Id` header, falling back to a demo user. The deps live in `src/soshop/auth/identity.py`.

`README.md` in this directory is the long-form reference (env vars, MinIO setup, database setup, dispatch-metadata schema). This file is the everyday cheat sheet.

## Commands

```bash
uv sync                                          # install (dev group included by default)
uv run uvicorn soshop_server.api.app:app --port 8000
uv run ruff check src/ tests/                    # lint
uv run ruff format src/ tests/                   # format
uv run mypy src/                                 # type check
uv run pytest                                    # tests (hermetic, no external services)
```

CI (`.github/workflows/server-ci.yml`) runs all four (lint, format check, mypy, pytest) on every push.

## Layout

```
src/soshop_server/        # API shell: app factory, config, db pool, asset helpers, agent entrypoint
src/soshop/               # agent pipeline + commerce API (agent, livekit, routes, services)
migrations/               # raw SQL (numbered .up.sql / .down.sql), apply via psql
tests/                    # pytest-asyncio, asyncio_mode = "auto"
```

No ORM. Write raw SQL through asyncpg. The connection pool is created lazily by `soshop_server.db.pool.get_pool()`.

## Conventions

- **Linter is the style guide.** Ruff (line length 100, McCabe max complexity 10, security/bugbear/pyupgrade rules on). Let it tell you. Do not hand-format.
- **Hermetic by default.** A test that needs a real DB or S3 must `pytest.skip` when `DATABASE_URL` / `BUCKET_NAME` are unset. Mocked DB tests are forbidden. Past mocked-DB tests masked migration breakage.
- **Schema changes ship as numbered SQL.** Add `migrations/000N_<name>.up.sql` plus `.down.sql`, apply with `psql "$DATABASE_URL" -f ...`. There is no migration framework.
- **Config goes through `Settings`.** Do not read `os.environ` directly in app code. Add the field to `soshop_server.core.config.Settings` so it picks up `.env` and gets type-checked.

## Postgres + asyncpg, non-obvious

The default `DATABASE_URL` is the **pooled** connection (on Neon the host ends `-pooler.<region>.aws.neon.tech`). The pooler runs in transaction-pool mode and does not pin prepared statements to a backend, so `pool.get_pool()` sets `statement_cache_size=0`. Do not re-enable it.

Pool caps are `min_size=1`, `max_size=10`, small on purpose because the pooler multiplexes on the database side.

## Agent dispatch metadata

The agent rejects free-form dispatch metadata. The required schema lives in `src/soshop/agent/schemas.py::DispatchMetadata` (`persona_id`, `script_id`, `session_id`, `user_id`, `pitch_window_ms`, `product_id`, `popup_payload`). Always dispatch through `soshop.livekit.dispatch.create_validated_dispatch` / `build_dispatch_request` so the payload is validated *before* it reaches LiveKit. Otherwise the worker shuts down at startup, which is the intended fail-fast behavior.

## Persona swap, in-place by default

`/feed/swipe` mutates the running agent in place. The agent process spans the whole feed session: one LiveKit room, one LemonSlice avatar session, one ElevenLabs TTS instance. On swipe the route layer sends a `swap_persona` JSON message on the `agent_control` topic via `soshop.livekit.control.send_agent_control`. The agent's `_execute_swap_persona` handler in `soshop/agent/main.py` interrupts current TTS, reloads persona/voice/avatar from Postgres, calls `Agent.update_instructions` plus `tts.update_options` plus the LemonSlice control endpoint (`soshop.livekit.lemonslice_control.lemonslice_update_image`), rebinds `PitchTools`, and kicks off the new opening line.

A legacy "redispatch a fresh agent" flow (`swap_persona_in_room`) is still wired up as a fallback. Set `FEED_SWAP_MODE=single_room` (or `ping_pong` for the sibling-room scheme) to use it. The default is `in_place`.

## Channel rotation, completion-driven not timer-driven

The always-on channel loop (`soshop.agent.rotation.PitchOrchestrator`) drives swaps off the `SpeechHandle` returned by `AgentSession.generate_reply`. The swap path stashes the handle on `runner.current_pitch_handle`, the orchestrator awaits it, then runs a 300 to 700 ms jitter pause before queuing the next ad. `ads.duration_ms` is a *safety ceiling* (default 45 s). It force-rotates if the LLM or TTS hangs, but it is not the target segment length. Pitch authoring targets a **30 to 45 combined-word band** (hook + script_text + cta), which is about 15 s of TTS at ElevenLabs' default of roughly 150 WPM. The band is enforced by `tests/seed/test_persona_word_count.py`. See `server/seed/personas/README.md` for the author-facing contract.

## Live studio, a director loop not a single prompt

`/studio` sessions dispatch with `live_session_id` set and run `soshop/agent/live_host.py` instead of the pitch pipeline. `LiveDirector` (`soshop/agent/live_director.py`, no LiveKit or asyncpg imports, unit-tested with fakes) speaks one short turn at a time and applies studio commands between turns. Every cue is a user turn tagged `[ĐẠO DIỄN]` / `[DIRECTOR]`; the system prompt in `live_prompts.py` tells the model to follow it and never read it aloud. Chat history is rebuilt before every turn from the last two turns that were spoken in full (`SessionSpeaker._load_history`): interrupted or garbled replies never go back to the model, because it imitates them. That small window also keeps requests inside free-tier token-per-minute limits, which is why product facts are repeated on every product cue. See the "Live studio" section of `README.md` for the request flow and the local-setup traps (LiveKit `--node-ip`, worker restart, per-loop DB pool).

## Review videos, offline and per frame

`soshop/advideo/` renders pre-recorded TikTok-style reviews (`POST /studio/videos`, or `python -m soshop.advideo`). It reuses the live avatar's pieces (LivePortrait, gestures in `lipsync/motion.py`, the accent detector and body warp in `lipsync/performance.py`) but renders every frame, so the mouth is driven by LivePortrait's lip retargeting rather than Wav2Lip. A render takes 10 to 15 minutes and runs in its own process, one at a time. Keep the logic in `script`, `voice`, `mouth`, `director` and `edit` free of model weights so `tests/advideo/` stays hermetic. See "Review videos" in `README.md`.

## Promo videos, Google Flow driven through the user's Chrome

`soshop/promo/` makes product videos with Google Flow (`/catalog/products`, `/promo/videos`, web page `/studio/videos`). Flow has no API: the SoShop Flow Bridge extension (`server/flow_extension/`) connects to `/promo/flow/bridge` and the API drives a background Flow tab over `chrome.debugger`. Keep two rules when touching `promo/flow.py`: interact through the in-page DOM helpers (`__soshop.act` / `hover`), because a never-painted background tab drops CDP mouse input and screenshots hang; and never approve a Flow cost quote without the one-clip, at-most-the-model-price check in `_approve`. `script`, `prompts`, `qa`, `assemble` and `jobs` stay free of browser code so `tests/promo/` is hermetic. See "Promo videos with Google Flow" in `README.md`.

## Livestream sessions, a derived timeline not a timer

`/broadcasts` plays a playlist of finished videos (`soshop/broadcast/`, `soshop/services/broadcasts.py`). The position on air is never stored as it moves: `broadcasts.playhead` is an anchor and `soshop.broadcast.timeline.locate` derives the rest. Two rules: every write that can change what plays (control actions and playlist edits alike) must re-anchor the playhead to "now" against the list as it was before the write, under the row lock (`_editing` does this); and `timeline.py` must stay in step with its TypeScript port `web/lib/broadcast/timeline.ts`, whose tests mirror `tests/broadcast/test_timeline.py`. See "Livestream sessions from pre-rendered videos" in `README.md`.

## API note

`GET /channels` is the current feed API. The older `POST /feed/session`, `POST /feed/swipe`, and `GET /feed/cursor` endpoints are kept for compatibility and the tests still cover them.
