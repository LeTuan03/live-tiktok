# SoShop Server

*Part of the [SoShop monorepo](../README.md).*

```bash
uv run uvicorn soshop_server.api.app:app --host 0.0.0.0 --port 8000
uv run python -m soshop_server.agent.main dev
```

The backend for the SoShop live-selling demo: a FastAPI + asyncpg API (raw SQL, no ORM) plus a LiveKit Agents worker that drives the AI hosts. The voice and video pipeline is Groq (LLM + STT), Silero (VAD), ElevenLabs (TTS), and LemonSlice (avatar). The app runs open access (no external auth provider; the portal resolves to a default organization), and an S3-compatible bucket (Tigris in production, MinIO locally) holds assets.

## Quick start

```bash
uv sync
cp .env.example .env
# fill in keys

# API server
uv run uvicorn soshop_server.api.app:app --host 0.0.0.0 --port 8000

# LiveKit agent worker (separate process, run in another terminal)
uv sync --group agent                              # agent deps are not in the default `dev` group
uv run python -m soshop_server.agent.main dev      # hot reload for local development
uv run python -m soshop_server.agent.main start    # production mode
```

Verify the server is up:

```bash
curl http://localhost:8000/healthz
# {"status":"ok"}
```

## Code quality

```bash
uv run ruff check src/ tests/      # lint
uv run ruff format src/ tests/     # format
uv run mypy src/                   # type check
uv run pytest                      # tests
```

CI runs lint, format check, type check, and tests on every push and pull request (`.github/workflows/server-ci.yml`).

### Test layout

`tests/` splits into fast, hermetic unit and route tests, plus explicitly opt-in integration tests:

| File | What it covers | Needs |
|---|---|---|
| `test_health.py` | `GET /healthz` | nothing |
| `test_assets_routes.py` | `POST /assets/upload-url` | nothing |
| `test_live_routes.py` | `POST /live/{room}/viewer-token` | nothing |
| `test_personas_routes.py` | `/personas/*`, `/favorites/*` (incl. 1-to-1 chat dispatch) | nothing |
| `test_feed_routes.py` | `/feed/session`, `/feed/swipe`, `/feed/event` | nothing |
| `test_room_lifecycle.py` | LiveKit dispatch helpers (1-to-many and 1-to-1) | nothing |
| `test_s3_helpers.py` | `soshop_server.assets` helpers with a mocked S3 client | nothing |
| `test_assets_smoke.py` | Live S3 round-trip (presigned PUT + public GET) | `BUCKET_NAME` + `AWS_*` (skips otherwise) |
| `test_db_integration.py`, `test_db_integration_extended.py` | Real Postgres read/write paths, FK cascades, CHECK constraints | `DATABASE_URL` (skips otherwise) |

Beyond the routes above, hermetic unit suites under `tests/agent/`, `tests/seed/`, `tests/api/`, `tests/db/`, and `tests/livekit/` cover the agent pipeline, completion-driven rotation, the persona word-count gate, SQL models, and channel logic.

Run the default (hermetic) suite:

```bash
uv run pytest                      # no external services
```

Include the live integration tests by exporting the relevant env vars first. With Postgres and an S3 bucket configured in `server/.env` (loaded by `pydantic-settings`):

```bash
# DB integration tests against a real Postgres branch
DATABASE_URL="$DATABASE_URL" uv run pytest tests/test_db_integration.py tests/test_db_integration_extended.py

# Live S3 round-trip
uv run pytest tests/test_assets_smoke.py
```

To skip the integration tests in an environment that already has those env vars set, filter by file:

```bash
uv run pytest --ignore=tests/test_db_integration.py --ignore=tests/test_db_integration_extended.py --ignore=tests/test_assets_smoke.py
```

The DB tests deliberately do not mock. Mocked DB tests have hidden migration breakage in the past. The integration tests open a transaction and roll it back, so the test branch never accumulates fixture data.

## Database (Postgres)

SoShop uses Postgres for user and app state. The demo is built against [Neon](https://neon.tech), but any Postgres works. Set the connection string in `server/.env` as `DATABASE_URL`. It is the only DB env var the server reads (`soshop_server.core.config.Settings.DATABASE_URL`).

### First-time setup

1. Create a Postgres database. On Neon, create a project at <https://console.neon.tech>.
2. Use the **pooled** connection string (on Neon the host ends in `-pooler.<region>.aws.neon.tech`). The pooled URL routes through pgbouncer, which is what the pool defaults are tuned for.
3. Paste it into `server/.env` as `DATABASE_URL=...`. The placeholder in `.env.example` shows the expected format. `.env` is git-ignored, so do not commit it.
4. Verify connectivity:
   ```bash
   psql "$DATABASE_URL" -c "select 1;"
   ```
5. Apply the schema, every numbered migration in order:
   ```bash
   for f in migrations/0*.up.sql; do psql "$DATABASE_URL" -f "$f"; done
   ```
6. Seed the demo host and catalog:
   ```bash
   uv run python scripts/seed_v1.py
   ```
   The seeder loads `seed/catalog.json` and `seed/personas/*.json` if present, otherwise the committed brand-free `*.example` templates, so a fresh clone has data to play. See [`seed/personas/README.md`](seed/personas/README.md).

### Connection pool

`soshop_server.db.pool.get_pool()` lazily creates a process-wide `asyncpg.Pool`. It disables asyncpg's prepared-statement cache (`statement_cache_size=0`) because Neon's pgbouncer runs in transaction-pooling mode and does not pin prepared statements to a backend. Caps are small by default (`min_size=1`, `max_size=10`) since the pooler multiplexes on the database side. If you run a plain Postgres without pgbouncer, these settings are still safe.

### Integration tests

Tests that need a real database read `DATABASE_URL` from the environment and skip when it is unset, so the default `uv run pytest` stays hermetic. To run the DB integration tests:

```bash
DATABASE_URL="$DATABASE_URL" uv run pytest tests/test_db_integration.py
```

## Observability

The server exposes a Prometheus `/metrics` endpoint (`soshop.observability.metrics`) with instruments for room and session counts, persona-swap latency, LemonSlice warmup, and agent-dispatch attempts and errors. `server/ops/` ships example Grafana dashboards and Prometheus alert rules built against those metric names, ready to adapt to your own monitoring stack.

## Environment variables

See `.env.example` for the full list. Required for runtime:

| Variable | Purpose |
|---|---|
| `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | LiveKit project |
| `DATABASE_URL` | Postgres (pooled connection string) |
| `DEFAULT_ORG_ID` | Advertiser-portal default org for open-access requests (defaults to `org_demo_shop`, the org seeded by the bundled example catalog) |
| `GROQ_API_KEY` | Groq (LLM + STT) |
| `ELEVEN_API_KEY` | ElevenLabs (TTS) |
| `LEMONSLICE_API_KEY` | LemonSlice avatar |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_ENDPOINT_URL_S3` / `BUCKET_NAME` | S3-compatible asset bucket |
| `S3_REGION` / `S3_PUBLIC_URL` | Region used for SigV4, plus the CDN-friendly base for public read URLs |
| `MEDIA_STORAGE` | `local` (default) or `s3`: where studio uploads and promo video files live, see [Media storage](#media-storage-local-or-s3) |

`DATABASE_URL` and the provider keys (LiveKit, Groq, ElevenLabs, LemonSlice) are what the live demo needs. The `AWS_*` / `BUCKET_NAME` / `S3_*` vars are used by the asset-upload and `/customize` avatar flows, and by the studio once `MEDIA_STORAGE=s3`; with the default `local` the app boots and serves everything without them.

**Running without ElevenLabs or LemonSlice keys.** Only `GROQ_API_KEY` is strictly required. With `ELEVEN_API_KEY` blank the agent speaks through keyless voices (`EDGE_TTS_VOICE`, an Edge voice name or `google-<lang>`, see `soshop/agent/edge_voice.py`). With `LEMONSLICE_API_KEY` blank the agent publishes the persona image itself as a gently animated 9:16 video track (`soshop/agent/still_avatar.py`), with no lip sync. On Groq's free tier also set `CHANNEL_MIN_SLOT_SECONDS=20` so the rotation stays under the tokens-per-minute limit. The `/customize` voice picker still needs an ElevenLabs key.

## Live studio

`/studio` in the web app is a host console for running a real livestream with an AI model. The backend for it lives in `soshop/api/routes/studio.py` (REST under `/studio`), `soshop/services/studio*.py` (data, uploads, script writing) and `soshop/agent/live_*.py` (the on-air behavior). Schema: `migrations/0002_live_studio.up.sql`.

How a session runs:

1. `POST /studio/sessions/{id}/start` creates the LiveKit room `live-<id>`, dispatches the worker with `live_session_id` in the dispatch metadata and returns a join token.
2. The worker runs `run_live_session` (`soshop/agent/live_host.py`): an LLM + TTS pipeline with no STT, plus the avatar. `LiveDirector` (`soshop/agent/live_director.py`) then drives the host turn by turn: free talk and viewer comments while `chatting`, the ordered product queue while `presenting` (each product in three short turns), silence while `paused`.
3. `POST /studio/sessions/{id}/control` relays `start_products`, `pause`, `resume`, `skip`, `present`, `stop_products` and `say` to the agent over the `agent_control` data topic. Viewers chat on the `live_chat` text-stream topic and the agent answers them by name.
4. The agent writes its state to `live_sessions.live_state` every few seconds and broadcasts it on `live_state`. A live session whose state goes quiet for 75 s is marked ended, so a crashed worker never leaves the studio stuck on "live".
5. `POST /studio/sessions/{id}/end` asks the host to say goodbye, then deletes the room.

Uploaded images go to the media store (see [Media storage](#media-storage-local-or-s3)): `/media/uploads/<name>` on local storage, the bucket's public URL on S3. Motion-avatar assets and model weights are still plain files under `DATA_DIR` (default `server/data/`, git-ignored) on the machine that builds and plays them.

What the host looks and sounds like, without provider keys:

- **Avatar.** `soshop/agent/lipsync/` builds a looping motion clip from the model's portrait once (LivePortrait, about 14 minutes on a laptop, started automatically when a model is created) and lip-syncs its mouth to the TTS audio in real time (Wav2Lip on the Intel GPU through OpenVINO). The clip has an idle and a talk track with different head and face behavior, and a realtime warp adds breathing, body sway, a lean toward the camera, nods on stressed syllables and a slow camera drift. Hands never move. Avatars built before the two-track format still play (one track, with the realtime warp); rebuild them from the studio to get the talk track. A portrait with an open mouth is closed to a relaxed smile first, so the model does not idle on a frozen laugh. Under CPU load the mouth is re-inferred only every 2nd or 3rd frame so audio never falls behind. A model without built assets falls back to the still image. Read `soshop/agent/lipsync/README.md` before using this commercially: the face detector and the Wav2Lip weights are licensed for non-commercial research only.
- **Voice.** Keyless TTS in `soshop/agent/edge_voice.py`, per model. Vietnamese defaults to `google-vi`, the Google Translate voice: a native speaker, and the only keyless option that answered every request (about 0.4 s to first audio). The Edge `vi-VN` voices sound more natural but the endpoint dropped between a third and nearly all of their requests when measured, and racing parallel requests made it worse; they stay selectable, with a per-sentence fallback. The Edge multilingual voices are reliable but read Vietnamese with a foreign accent. English uses the Edge voices.
- **LLM.** `GROQ_LLM_MODEL` on Groq. Only fully spoken turns are fed back as chat history (the last two). A reply cut by pause or skip, left in the context, makes the model answer in fragments and then in tool-call debris, so interrupted turns are dropped and anything that looks like structured output is never sent to TTS.

Local setup notes that are easy to trip over:

- **LiveKit in Docker must advertise a reachable address.** Run it with `--node-ip 127.0.0.1` and publish `7881/tcp` and `7882/udp`. Otherwise browsers connect to signaling, never receive media and drop out with "leave request while trying to reconnect". `dev.ps1` at the repo root creates the container correctly.
- **Restart the worker after restarting LiveKit.** A worker that lost its LiveKit connection keeps registering but fails every job.
- **The worker accepts jobs regardless of CPU load in development.** In any other `ENVIRONMENT` it sheds jobs above `AGENT_LOAD_THRESHOLD` (default 0.7).
- **One asyncpg pool per event loop.** On Windows the worker runs each job on its own thread and loop, so `get_pool()` hands every loop its own pool.
- **Starting confirms the host joined.** `POST /studio/sessions/{id}/start` waits for the agent participant, re-dispatches up to three times and returns 503 with a clear message if no worker picked the job up, instead of reporting a stream that never appears.


## Review videos (pre-rendered)

`soshop/advideo/` turns a studio product and model into a finished 1080x1920 TikTok-style review video, offline. Nothing runs in real time, so every frame gets the full LivePortrait render, including the mouth: the jaw and the portrait's own teeth move at full resolution instead of a 96 px Wav2Lip patch. The steps are script (Groq, JSON beats with a gesture and a product cue each, template fallback), voice (each beat spoken separately for exact timings), director (gestures per beat, 3D nods on stressed syllables, blinks at phrase starts, mouth opening from the first-formant band), render, and edit (cuts and punch-ins per beat, captions with the spoken word highlighted, hook title, product cutaway or card, price and buy button). The package docstrings explain each step.

- **API.** `POST /studio/videos {model_id, product_id}` spawns `python -m soshop.advideo --request ...` into `data/videos/<id>/` and returns the job. `GET /studio/videos[/{id}]` reports `status` and `progress`, `GET /studio/videos/{id}/file` and `/thumb` serve the results, `DELETE` removes a finished job. One render at a time: a second request gets 409.
- **CLI.** `uv run --group agent python -m soshop.advideo --portrait <photo> --name "<product>" --price 199000 --description "..." --highlights "a; b; c" [--product-image <photo>] --out <dir>`. `--script script.json` speaks an edited script, `--reuse-host` skips the render when only the edit changed.
- **Cost.** About 1.2 s per frame on the dev laptop, so 20 to 30 s of video takes 10 to 15 minutes. It needs `ffmpeg` on `PATH` and the `agent` dependency group. Captions use a system bold font; set `ADVIDEO_FONT` on machines without Segoe UI, Arial, Noto or DejaVu.
- **Limits.** The host's hands never move and she cannot hold the product. The script prompt forbids invented claims and fake personal experience, and TikTok expects AI-generated videos to carry its AI-generated content label.

## Promo videos with Google Flow

`/studio/videos` in the web app makes product promo videos in which an AI host holds, uses and talks about the product. The video itself is generated by Google Flow (Veo 3.1 or Omni 1.2 Flash) on the user's own Flow account and credits. Code: `soshop/promo/`, routes in `soshop/api/routes/promo.py`, catalog fields in `migrations/0003_catalog_promo.up.sql`.

1. **Catalog.** `GET/POST/PATCH/DELETE /catalog/products` reads and writes the existing `products` table plus brand, category, highlights and usage notes. The feature image (`product_media` position 0) is what Flow gets as the product reference, so it must be a real photo; seed rows that only have a picsum placeholder are flagged `image_is_placeholder`.
2. **Script.** `POST /promo/videos {product_id, model_id, shots, language, model}` asks `PROMO_LLM_MODEL` on Groq for a JSON script: per 8 s shot an English action, camera and emotion, and one spoken line in the host's language. `soshop.promo.script` rejects wrong shot counts, lines too long for 8 s and instant-result claims the facts do not make, with up to two corrective retries. The draft costs no credits and can be edited (`PUT /promo/videos/{id}/script`).
3. **Render.** `POST /promo/videos/{id}/render` runs the job inside the API process (`soshop.promo.runner`, one at a time). `soshop.promo.flow` opens a background Flow tab in the user's Chrome, creates a project, uploads the host portrait and the product photo, and per shot opens a fresh agent session, attaches both images, types the prompt from `soshop.promo.prompts`, checks the quoted cost (one video, at most the model's price) before approving it, waits for the new video tile and downloads the MP4 through DevTools' `Network.loadNetworkResource`. Each clip is then transcribed with Whisper (`PROMO_TRANSCRIBE_MODEL`) and scored against the scripted line (`speech_match`).
4. **Assemble.** ffmpeg normalises the clips to 720x1280, appends a 2.5 s end card (product photo, name, price, buy button) and writes `video.mp4`, `thumb.jpg` and a WebVTT caption track timed by the measured speech.

The job state is a row in `promo_videos` (`migrations/0006_promo_videos.up.sql`), so every API machine sees the same videos. The files are in the media store under `promo/<id>/`: each clip is uploaded as soon as Flow hands it over, and a re-rendered file gets a new name so a CDN never serves an old take. Rendering works in `DATA_DIR/work/promo/<id>/`, dropped once the video is ready. Shots that already have a clip are kept, so retrying a failed job (on any machine) or `POST /promo/videos/{id}/shots/{n}/regenerate` only spends credits on what is missing. `GET /promo/videos/{id}/file`, `/thumb` and `/shots/{n}/clip` serve local files or redirect to the bucket; `/captions` is always served by the API, because a `<track>` must be same-origin or CORS.

**How the server reaches Flow.** Flow has no public API and its requests need the browser's sign-in and reCAPTCHA. The SoShop Flow Bridge extension (`server/flow_extension/`, load it unpacked in the Chrome you use for Flow) dials into `ws://127.0.0.1:8000/promo/flow/bridge` and runs what the server sends on `flow.google.com` tabs only, through `chrome.debugger`. The WebSocket accepts only `chrome-extension://` origins. `GET /promo/flow/status` reports whether the extension is connected.

Things that cost time to find out:

- **The job tab is never painted.** Chrome does not render a background tab, so CDP mouse input is dropped (no hit-test data) and screenshots, which would force a paint, hang on and off. The driver therefore clicks and hovers with DOM events from inside the page; only text input, file inputs and downloads use CDP. `flow-error.txt` in the job's work directory records the page text when a step fails; `flow-error.png` is best effort and is kept in the store for `GET /promo/videos/{id}/debug`.
- **Clicks can land too early.** Flow's Angular app ignores clicks while it boots, so every state-changing click is retried until its effect shows (`_click_until`).
- **The video URL redirects to googlevideo.com**, which page `fetch` cannot read (CORS). `Network.loadNetworkResource` loads it with the page's cookies instead.
- **Flow's agent asks before spending credits** in its default "always confirm" mode; the driver reads the quote and refuses anything other than one clip at the expected price. Measured on 2026-11-15: Veo 3.1 Lite 10 credits per 8 s clip, Omni 1.2 Flash 24, Veo 3.1 Fast 20, Veo 3.1 Quality 100. Flow retires Veo 3.1 Fast and Quality on 2026-12-05.
- **Automating Flow is not an official integration.** It drives the Flow web app as the signed-in user would, at one generation at a time, and can break when Flow's UI changes. Google could treat automated use of an account as unusual activity.

Optional behavior knobs all have sane defaults and are documented inline in `.env.example`: `AGENT_NAME` (the worker name dispatches target; override when a local worker runs alongside a deployed one), `CHANNEL_COUNT` (always-on channel count), `FEED_SWAP_MODE` (`in_place` vs the legacy `single_room` / `ping_pong` redispatch), `LEMONSLICE_IDLE_TIMEOUT`, `AGENT_SESSION_DURATION_MINUTES`, and `OTEL_*` tracing.

## Livestream sessions from pre-rendered videos

`/studio/live` in the web app builds a livestream out of finished videos instead of a real-time AI host: pick promo videos (or upload intro, outro and product clips), order them, set each one's time on air and the loop mode, press Start. Code: `soshop/broadcast/` (pure logic and media), `soshop/services/broadcasts.py` (data and control), routes in `soshop/api/routes/broadcasts.py`. Schema: `migrations/0005_broadcasts.up.sql`. Product talking points ("Nội dung cần giới thiệu", fed to the promo script as facts) arrived with it in `0004_product_talking_points.up.sql`.

- **Playlist.** An optional intro played once, the product videos in order, an optional outro played once. Each item stays on air for `duration_s` seconds (repeating its video), or for exactly one play when `duration_s` is null. `loop_mode` is `continuous` (repeat until stopped) or `count` (`loop_count` passes, then the outro, then the end). Items whose video is missing or being re-rendered are skipped, and a promo video used by a session cannot be deleted.
- **Playback is derived, never ticked.** `broadcasts.playhead` stores where playback stood at one instant (`at_ms`, item, offset, playing). `soshop.broadcast.timeline.locate` walks forward from it through the slot lengths to find what is on air at any later instant, so nothing runs on the server while a show plays and a restart loses nothing. Control actions (`POST /broadcasts/{id}/control`: start, pause, resume, next, prev, jump, finish, stop) and every playlist edit re-anchor the playhead to "now" under a row lock; an edit keeps the current video playing from the same second. A show that runs off its end is marked ended the next time anyone reads it.
- **Players.** Every response carries `playback` with `server_time_ms`. The web player (`web/lib/broadcast/timeline.ts`, the same algorithm in TypeScript, same tests) computes the position locally, keeps two `<video>` elements so the next slot is buffered before the swap, and re-seeks only when it drifts more than a second. `GET /broadcasts/{id}/events` is a server-sent event stream that re-sends the broadcast on every change; each stream closes after 25 s and the browser reconnects, so it never holds up a server shutdown.
- **Going out to TikTok Live or YouTube Live.** `/onair/{id}` in the web app is a 9:16 page with only the video (plus a product card, hidden with `?clean=1`). Add it to OBS Studio as a Browser source and stream with the platform's stream key. A server-side RTMP output would follow the same timeline (`locate` gives the file and offset for any instant); it is not built.
- **Uploads.** `POST /broadcasts/uploads` accepts H.264, AV1 (remuxed to MP4 with the index up front) or VP8/VP9 WebM up to 500 MB, checks and remuxes it in `DATA_DIR/work/uploads/`, then puts it in the media store under `uploads/` with a thumbnail and a JSON sidecar holding the measured length. Needs `ffmpeg` on `PATH`.

## Media storage (local or S3)

Studio uploads (portraits, product photos, intro and outro videos) and promo video files go through `soshop_server.storage`. Each file has a key such as `uploads/<uuid>.jpg` or `promo/<id>/video-<rev>.mp4`, and `MEDIA_STORAGE` picks where the keys live:

| `MEDIA_STORAGE` | Files | Browsers read from |
|---|---|---|
| `local` (default) | `DATA_DIR/<key>` | the API at `/media/<key>` (range requests, only the `uploads/` and `promo/` folders) |
| `s3` | `BUCKET_NAME/<key>` | `S3_PUBLIC_URL/<key>`, a CDN in front of the bucket. `/media/<key>` redirects there |

Keys are never reused for different bytes, so objects are sent with `Cache-Control: public, max-age=86400`. A deploy that runs more than one API machine, or a machine with an ephemeral disk such as Fly, needs `s3`. The agent worker and the renderers fetch what they need into `DATA_DIR/cache/` and need the same `AWS_*` variables. `s3` also needs `BUCKET_NAME` and `S3_PUBLIC_URL`; the server refuses to start without them. The bucket must allow public reads (see Tigris and MinIO below).

**Moving existing data.** After applying migration 0006, import the promo jobs that still live in `data/promo/<id>/job.json`:

```bash
psql "$DATABASE_URL" -f migrations/0006_promo_videos.up.sql
uv run python scripts/migrate_media.py              # job.json -> promo_videos rows
MEDIA_STORAGE=s3 uv run python scripts/migrate_media.py --upload   # then copy files to the bucket
```

Both steps are safe to repeat and accept `--dry-run`. URLs already stored in the database (`/media/uploads/...`) need no rewrite: on S3 the API redirects them to the bucket.

Still local to one machine, until heavy jobs move to a worker: motion-avatar assets (`DATA_DIR/avatars/`), review videos (`DATA_DIR/videos/`) and model weights (`DATA_DIR/models/`).

## Asset hosting (S3-compatible)

The same bucket also serves the older presigned-upload flow. The server talks to it through `soshop_server.assets`:

- `upload_bytes(key=..., data=..., content_type=...)` for server-side uploads.
- `generate_presigned_put_url(key=..., content_type=...)` for short-lived signed URLs so clients can upload directly to the bucket (used by `POST /assets/upload-url`).
- `public_url_for_key(key)` turns an object key into the public read URL.

Public URLs are built from `S3_PUBLIC_URL`, so you can put a CDN in front of the bucket later without a code change. If `S3_PUBLIC_URL` is unset, the helper falls back to `<endpoint>/<bucket>/<key>`, which works against Tigris or MinIO out of the box.

### Production: Tigris

Configure the four `AWS_*` and `BUCKET_NAME` vars from the Tigris dashboard, set `S3_PUBLIC_URL` to the public bucket URL (or your CDN), and leave `S3_REGION=auto`.

### Local dev: MinIO

You need only one of these, a real bucket or MinIO. The server does not care which. The fastest way to get going locally is MinIO in Docker:

```bash
docker run -d --name soshop-minio \
  -p 9000:9000 -p 9001:9001 \
  -e MINIO_ROOT_USER=minioadmin \
  -e MINIO_ROOT_PASSWORD=minioadmin \
  minio/minio server /data --console-address ":9001"

# Create the bucket and make it publicly readable so `S3_PUBLIC_URL` works.
docker run --rm --network host minio/mc sh -c '
  mc alias set local http://localhost:9000 minioadmin minioadmin &&
  mc mb -p local/soshop-dev &&
  mc anonymous set download local/soshop-dev
'
```

Then in `server/.env`:

```
AWS_ACCESS_KEY_ID=minioadmin
AWS_SECRET_ACCESS_KEY=minioadmin
AWS_ENDPOINT_URL_S3=http://localhost:9000
BUCKET_NAME=soshop-dev
S3_REGION=auto
S3_PUBLIC_URL=http://localhost:9000/soshop-dev
```

### Smoke test

`tests/test_assets_smoke.py` uploads a tiny PNG via `upload_bytes` and fetches the public URL with `httpx`. It skips automatically when the S3 env vars are unset, so the default `uv run pytest` stays hermetic. Run it after configuring MinIO (or a real bucket):

```bash
uv run pytest tests/test_assets_smoke.py -v
```

## Project structure

```
server/
├── pyproject.toml
├── Dockerfile.api                 # FastAPI / uvicorn image (Fly.io)
├── Dockerfile                     # LiveKit Agents worker image
├── .env.example
├── migrations/                    # numbered raw SQL (.up.sql / .down.sql), applied via psql
├── seed/personas/                 # persona seed JSON + authoring contract (see its README)
├── scripts/
│   ├── seed_v1.py                 # idempotent persona/product/channel seeder (UUID5 upserts)
│   ├── preload_curated_voices.py  # copies curated ElevenLabs voices into your account for /customize
│   └── dump_openapi.py            # writes openapi.json for the web SDK codegen
├── ops/                           # example Grafana dashboards + Prometheus alert rules
├── src/soshop_server/             # API shell: app factory, config, db pool, asset helpers
└── src/soshop/                    # agent pipeline + commerce API (agent, livekit, routes, services)
tests/                             # pytest-asyncio, hermetic by default
```

## Agent dispatch metadata

The agent is dispatched with a strictly validated JSON metadata payload (`soshop.agent.schemas.DispatchMetadata`). Fields:

- `persona_id`, `script_id`, `session_id`, `user_id` (non-empty strings)
- `pitch_window_ms` (non-negative int; advisory, retained for transport compatibility, completion-driven rotation no longer reads it)
- `product_id` (string or `null`)
- `popup_payload` (object with `id`, `title`, `image_url`, `cta`, `deep_link_url`, `landing_url`, an optional `media` gallery array, or `null`)
- `channel_id` (string or `null`) and `ad_start_index` (non-negative int, required when `channel_id` is set): present for an always-on channel-broadcast dispatch; a 1:1 companion dispatch leaves both unset

Always dispatch through `soshop.livekit.dispatch.create_validated_dispatch` (or `build_dispatch_request`) so the metadata is validated before it reaches LiveKit. An invalid payload makes the worker shut down at startup, which is the intended fail-fast behavior.
