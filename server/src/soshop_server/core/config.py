"""Application settings, loaded from the environment via pydantic-settings."""

from __future__ import annotations

from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Deployment environment. "development" enables local-only credential
    # fallbacks (see `_resolve_livekit_credentials`); set to "production" or
    # any other value in deployed envs so missing secrets fail fast instead
    # of silently using well-known dev credentials.
    ENVIRONMENT: str = "development"

    # LiveKit (separate project from soshop)
    LIVEKIT_URL: str = "ws://localhost:7880"
    # API key/secret default to "devkey"/"secret" *only* in development so
    # local dev against the bundled livekit-server still boots. In any other
    # environment, leaving them unset raises at startup rather than silently
    # signing tokens with well-known credentials.
    LIVEKIT_API_KEY: str = ""
    LIVEKIT_API_SECRET: str = ""
    # The agent_name the worker registers under and that dispatches target.
    # They MUST match for LiveKit to route a dispatch to this worker. Override
    # this in local dev (e.g. `AGENT_NAME=plug-agent-local`) when a deployed
    # worker is also registered on the same LiveKit project, otherwise LiveKit
    # load-balances dispatches across both and some land on the remote worker.
    AGENT_NAME: str = "plug-agent"
    # CPU load (0 to 1) above which the worker stops accepting new jobs.
    # Ignored when ENVIRONMENT is "development", where the worker always accepts.
    AGENT_LOAD_THRESHOLD: float = 0.7

    # Database (separate Neon project from soshop)
    DATABASE_URL: str | None = None

    # Comma-separated list of origins permitted by the CORS layer. The web
    # consumer feed and advertiser portal both call the API cross-origin
    # from a browser, so the OPTIONS preflight needs an explicit allow-list.
    # Set to "*" to allow any origin (note: that disables credentialed CORS
    # per the spec — fine for the public consumer feed which is anon-auth).
    CORS_ALLOW_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"

    # Default organization for the advertiser portal. The portal runs without
    # an external auth provider; each request resolves to this org unless an
    # `X-Org-Id` header overrides it. Defaults to the org seeded by the bundled
    # example catalog (`seed/catalog.json.example`) so a fresh clone shows real
    # portal data out of the box. Override in `.env` when seeding a custom org.
    DEFAULT_ORG_ID: str = "org_demo_shop"

    # LLM / STT
    GROQ_API_KEY: str | None = None
    # Groq chat model for the pitch LLM. Groq retires models regularly (the
    # previous pin, llama-3.3-70b-versatile, now returns 404), so this is
    # configurable. Pick a non-reasoning model: the pitch turn is capped at
    # 120 completion tokens and reasoning tokens would eat that budget.
    GROQ_LLM_MODEL: str = "qwen/qwen3.8-27b"

    # TTS
    ELEVEN_API_KEY: str | None = None
    # Voice for the keyless Edge TTS fallback, used when ELEVEN_API_KEY is
    # unset. Any Edge voice name works, e.g. "vi-VN-HoaiMyNeural".
    EDGE_TTS_VOICE: str = "en-US-AriaNeural"

    # Avatar (LemonSlice)
    LEMONSLICE_API_KEY: str | None = None
    # Idle timeout (seconds) requested when starting a LemonSlice avatar
    # session. -1 disables the idle teardown so the channel agent's avatar
    # stays warm across long stretches of empty rooms.
    LEMONSLICE_IDLE_TIMEOUT: int = -1

    # Always-on channel rotation.
    # `CHANNEL_COUNT` is reconciled on FastAPI startup; the same reconciler
    # is exposed via an admin endpoint so the value can be tuned
    # at runtime without a redeploy.
    CHANNEL_COUNT: int = 1
    # Minimum seconds one channel spends on an ad before rotating. 0 keeps the
    # purely completion-driven cadence. Set it (about 20) on a rate-limited
    # LLM tier: each pitch request sends the full persona prompt, and a pitch
    # that fails fast would otherwise retry in a tight loop.
    CHANNEL_MIN_SLOT_SECONDS: float = 0.0
    # Public-test gate — also serves as the bearer token the web's
    # /api/agents/session proxy forwards to /agents/session here.
    SITE_PASSWORD: str | None = None
    # Wind-down timeout for an on-demand agent session. Cap on LemonSlice
    # avatar cost when a tester forgets to close the tab.
    AGENT_SESSION_DURATION_MINUTES: int = 10

    # Local runtime data for the live studio: uploaded images, motion-avatar
    # assets and downloaded model weights. Relative paths resolve against the
    # process working directory (`server/`). Git-ignored.
    DATA_DIR: str = "data"

    # Bold .ttf with Vietnamese glyphs for the captions and cards burned into
    # pre-rendered ad videos (`soshop.advideo`). Unset: a system font is used
    # (Segoe UI Black or Arial Bold on Windows, Noto or DejaVu on Linux).
    ADVIDEO_FONT: str | None = None

    # Promo videos made with Google Flow (`soshop.promo`). The script LLM runs
    # on Groq and answers in JSON; a reasoning model writes far better staging
    # than the small chat model used on air.
    PROMO_LLM_MODEL: str = "openai/gpt-oss-120b"
    # Default Flow video model for new promo videos, named as in Flow's model
    # menu: "Veo 3.1 - Lite" (10 credits per 8 s clip), "Omni 1.2 Flash" (24),
    # "Veo 3.1 - Fast" (20) or "Veo 3.1 - Quality" (100). Flow retires Fast
    # and Quality on 2026-12-05. The studio can pick another per video.
    PROMO_FLOW_MODEL: str = "Veo 3.1 - Lite"
    # Whisper model on Groq that checks each clip says the scripted line.
    PROMO_TRANSCRIBE_MODEL: str = "whisper-large-v3"
    # Give up on one shot after this long in Flow's queue.
    PROMO_SHOT_TIMEOUT_S: float = 600.0

    # OpenTelemetry tracing — disabled by default. When `OTEL_ENABLED` is
    # true the FastAPI auto-instrumentor is installed; if a non-empty
    # `OTEL_EXPORTER_OTLP_ENDPOINT` is also set, spans are batch-exported
    # via OTLP/HTTP to that endpoint.
    OTEL_ENABLED: bool = False
    OTEL_EXPORTER_OTLP_ENDPOINT: str | None = None
    OTEL_SERVICE_NAME: str = "soshop-server"

    # S3 / S3-compatible asset storage (Tigris in prod, MinIO in local dev).
    # `BUCKET_NAME` + `AWS_*` are the canonical access creds (matches Fly.io
    # Tigris convention); `S3_PUBLIC_URL` is the CDN-friendly base used for
    # public read URLs returned to clients (leaves room for a CDN later
    # without a code change). `S3_REGION` is required by some signing flows
    # even when the endpoint is S3-compatible (MinIO/Tigris).
    AWS_ACCESS_KEY_ID: str | None = None
    AWS_SECRET_ACCESS_KEY: str | None = None
    AWS_ENDPOINT_URL_S3: str | None = None
    BUCKET_NAME: str | None = None
    S3_REGION: str = "auto"
    S3_PUBLIC_URL: str | None = None

    model_config = {"env_file": (".env", ".env.local"), "extra": "ignore"}

    @model_validator(mode="after")
    def _resolve_livekit_credentials(self) -> Settings:
        """Fill LiveKit dev credentials only in development; otherwise require
        explicit env values so a deploy can't silently boot with `devkey`/
        `secret`."""
        is_dev = self.ENVIRONMENT == "development"
        if not self.LIVEKIT_API_KEY:
            if not is_dev:
                raise ValueError(
                    f"LIVEKIT_API_KEY is not configured (ENVIRONMENT={self.ENVIRONMENT!r})"
                )
            self.LIVEKIT_API_KEY = "devkey"
        if not self.LIVEKIT_API_SECRET:
            if not is_dev:
                raise ValueError(
                    f"LIVEKIT_API_SECRET is not configured (ENVIRONMENT={self.ENVIRONMENT!r})"
                )
            self.LIVEKIT_API_SECRET = "secret"
        return self


settings = Settings()
