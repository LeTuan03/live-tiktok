-- Migration 0001 (up): initial schema.
--
-- Creates the full database structure for the SoShop server:
--   advertisers, products, product_media,
--   campaigns, campaign_products,
--   personas, persona_scripts,
--   feed_sessions, feed_events, favorites,
--   users, app_state,
--   ads, channels, channel_ad_log
--
-- Apply to a fresh database via the Neon SQL editor or:
--   psql "$DATABASE_URL" -f migrations/0001_initial_schema.up.sql
--
-- Then load demo data with:
--   DATABASE_URL=... uv run python scripts/seed_v1.py

BEGIN;

-- ---------------------------------------------------------------------------
-- advertisers
-- ---------------------------------------------------------------------------
CREATE TABLE advertisers (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    contact_email   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- products
-- ---------------------------------------------------------------------------
-- Ordered media (feature image, feature video, gallery) lives in
-- `product_media`; there is no single denormalized image column.
CREATE TABLE products (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    advertiser_id   UUID NOT NULL REFERENCES advertisers(id) ON DELETE CASCADE,
    sku             TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT,
    deep_link_url   TEXT,
    landing_url     TEXT,
    price_cents     BIGINT,
    currency        TEXT NOT NULL DEFAULT 'USD',
    status          TEXT NOT NULL DEFAULT 'draft'
                       CHECK (status IN ('draft', 'active', 'paused', 'archived')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (advertiser_id, sku)
);

CREATE INDEX idx_products_advertiser_status ON products(advertiser_id, status);

-- ---------------------------------------------------------------------------
-- product_media  (ordered image + video per product)
-- ---------------------------------------------------------------------------
-- Conventions:
--   * `position` is a stable 0-based ordering. Lower = earlier in the
--     gallery. By policy: position 0 is the feature image, position 1
--     is the feature video (if present), 2+ are gallery images.
--   * `kind` is constrained to `image` | `video`. Add new kinds (gif,
--     360, ar) by relaxing this CHECK.
--   * `(product_id, position)` is unique so the array order is enforced
--     relationally, with no client-side dedup required.
CREATE TABLE product_media (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id      UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    position        INTEGER NOT NULL CHECK (position >= 0),
    kind            TEXT NOT NULL CHECK (kind IN ('image', 'video')),
    url             TEXT NOT NULL,
    width           INTEGER,
    height          INTEGER,
    duration_ms     INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (product_id, position)
);

CREATE INDEX idx_product_media_product_position
    ON product_media (product_id, position);

-- ---------------------------------------------------------------------------
-- campaigns
-- ---------------------------------------------------------------------------
CREATE TABLE campaigns (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    advertiser_id     UUID NOT NULL REFERENCES advertisers(id) ON DELETE CASCADE,
    name              TEXT NOT NULL,
    budget_cents      BIGINT,
    daily_cap_cents   BIGINT,
    start_at          TIMESTAMPTZ,
    end_at            TIMESTAMPTZ,
    targeting         JSONB NOT NULL DEFAULT '{}'::jsonb,
    status            TEXT NOT NULL DEFAULT 'draft'
                          CHECK (status IN ('draft', 'scheduled', 'running',
                                            'paused', 'completed', 'archived')),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_campaigns_advertiser_id ON campaigns(advertiser_id);
CREATE INDEX idx_campaigns_status ON campaigns(status);
CREATE INDEX idx_campaigns_advertiser_status ON campaigns(advertiser_id, status);

-- ---------------------------------------------------------------------------
-- campaign_products  (many-to-many)
-- ---------------------------------------------------------------------------
CREATE TABLE campaign_products (
    campaign_id   UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    product_id    UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (campaign_id, product_id)
);

CREATE INDEX idx_campaign_products_product ON campaign_products(product_id);

-- ---------------------------------------------------------------------------
-- personas
-- ---------------------------------------------------------------------------
-- `voice_pipeline_id` is a free-text identifier for the voice pipeline the
-- agent should use (e.g. "elevenlabs-eleven_turbo_v2_5").
CREATE TABLE personas (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                TEXT NOT NULL,
    avatar_image_url    TEXT,
    voice_id            TEXT,
    voice_pipeline_id   TEXT NOT NULL,
    personality         TEXT,
    backstory           TEXT,
    traits              TEXT,
    persona_kind        TEXT NOT NULL DEFAULT 'host'
                            CHECK (persona_kind IN ('host', 'guest', 'expert', 'celebrity')),
    status              TEXT NOT NULL DEFAULT 'draft'
                            CHECK (status IN ('draft', 'active', 'paused', 'archived')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_personas_status ON personas(status);

-- ---------------------------------------------------------------------------
-- persona_scripts
-- ---------------------------------------------------------------------------
CREATE TABLE persona_scripts (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    persona_id    UUID NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    product_id    UUID REFERENCES products(id) ON DELETE SET NULL,
    script_text   TEXT NOT NULL,
    hook          TEXT,
    cta           TEXT,
    weight        INTEGER NOT NULL DEFAULT 1 CHECK (weight >= 0),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_persona_scripts_persona_id ON persona_scripts(persona_id);
CREATE INDEX idx_persona_scripts_product_id ON persona_scripts(product_id)
    WHERE product_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- feed_sessions
-- ---------------------------------------------------------------------------
-- user_id is the viewer identity (TEXT, no FK). Anonymous browsers get a
-- synthesized `anon-<hex>` id; the column stays free-text so it can hold
-- identifiers minted by any future auth provider.
CREATE TABLE feed_sessions (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        TEXT NOT NULL,
    room_name      TEXT,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at       TIMESTAMPTZ,
    device_id      TEXT,
    app_version    TEXT,
    country        TEXT
);

CREATE INDEX idx_feed_sessions_user_started ON feed_sessions(user_id, started_at DESC);
CREATE INDEX idx_feed_sessions_started_at ON feed_sessions(started_at DESC);

-- ---------------------------------------------------------------------------
-- feed_events
-- ---------------------------------------------------------------------------
CREATE TABLE feed_events (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id   UUID NOT NULL REFERENCES feed_sessions(id) ON DELETE CASCADE,
    persona_id   UUID NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    product_id   UUID REFERENCES products(id) ON DELETE SET NULL,
    event_kind   TEXT NOT NULL
                    CHECK (event_kind IN ('impression', 'view', 'view_complete',
                                          'tap', 'add_to_cart', 'purchase', 'skip',
                                          'favorite', 'unfavorite', 'share',
                                          'mute', 'unmute')),
    value_ms     BIGINT,
    ts           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_feed_events_session_id ON feed_events(session_id);
CREATE INDEX idx_feed_events_ts ON feed_events(ts DESC);
CREATE INDEX idx_feed_events_persona_ts ON feed_events(persona_id, ts DESC);
CREATE INDEX idx_feed_events_product_kind ON feed_events(product_id, event_kind)
    WHERE product_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- favorites
-- ---------------------------------------------------------------------------
CREATE TABLE favorites (
    user_id        TEXT NOT NULL,
    persona_id     UUID NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, persona_id)
);

CREATE INDEX idx_favorites_persona ON favorites(persona_id);

-- ---------------------------------------------------------------------------
-- users
-- ---------------------------------------------------------------------------
-- Local user catalog. Holds one row per known user so other tables can FK to
-- it and per-user preferences / settings can be persisted locally.
CREATE TABLE users (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_user_id TEXT NOT NULL UNIQUE,
    email            TEXT,
    display_name     TEXT,
    avatar_url       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- app_state
-- ---------------------------------------------------------------------------
-- Generic per-user JSONB key/value store for client-side app state that needs
-- to survive reinstalls (avatar preferences, onboarding flags, last-seen
-- cursors, etc.). Keyed by (user_id, key) so a single user can hold many
-- independent state blobs.
CREATE TABLE app_state (
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key         TEXT NOT NULL,
    value       JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, key)
);

CREATE INDEX idx_app_state_updated_at ON app_state(updated_at DESC);

-- ---------------------------------------------------------------------------
-- ads  (always-on channel rotation)
-- ---------------------------------------------------------------------------
-- One row per (persona x product x script) tuple, with an explicit
-- `sequence_order` for round-robin rotation. `duration_ms` is a safety
-- ceiling, not a target: the channel loop swaps personas when the host's
-- speech actually finishes (see soshop.agent.rotation) and only force-rotates
-- if the LLM or TTS hangs past this bound. Rows are populated by
-- `scripts/seed_v1.py`, not by this migration.
CREATE TABLE ads (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    advertiser_id   UUID NOT NULL REFERENCES advertisers(id) ON DELETE CASCADE,
    persona_id      UUID NOT NULL REFERENCES personas(id),
    product_id      UUID NOT NULL REFERENCES products(id),
    script_id       UUID NOT NULL REFERENCES persona_scripts(id),
    duration_ms     INTEGER NOT NULL DEFAULT 45000 CHECK (duration_ms > 0),
    sequence_order  INTEGER NOT NULL UNIQUE,
    status          TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'paused', 'retired')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX ads_sequence_active_idx
    ON ads (sequence_order)
    WHERE status = 'active';

-- ---------------------------------------------------------------------------
-- channels
-- ---------------------------------------------------------------------------
-- `id` is TEXT (e.g. "channel-0") because it doubles as the human-readable
-- LiveKit room name primary key. `current_ad_sequence` is mutable: only the
-- agent loop writes it as it advances through the rotation. `persona_id`
-- binds each channel to a fixed seller cycling through their own products;
-- it is nullable so the reconciler can back-fill the binding at boot.
CREATE TABLE channels (
    id                   TEXT PRIMARY KEY,
    ordinal              INTEGER NOT NULL UNIQUE,
    livekit_room_name    TEXT NOT NULL UNIQUE,
    current_ad_sequence  INTEGER NOT NULL,
    agent_dispatch_id    TEXT,
    persona_id           UUID REFERENCES personas(id),
    status               TEXT NOT NULL DEFAULT 'starting'
                             CHECK (status IN ('starting', 'running',
                                               'failed', 'retired')),
    last_heartbeat_at    TIMESTAMPTZ,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX channels_persona_idx ON channels(persona_id);

-- ---------------------------------------------------------------------------
-- channel_ad_log
-- ---------------------------------------------------------------------------
-- Append-only history of which ad each channel aired, for analytics and
-- repeat-threshold checks.
CREATE TABLE channel_ad_log (
    id           BIGSERIAL PRIMARY KEY,
    channel_id   TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    ad_id        UUID NOT NULL REFERENCES ads(id),
    started_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at     TIMESTAMPTZ
);

CREATE INDEX channel_ad_log_channel_idx
    ON channel_ad_log (channel_id, started_at DESC);

-- ---------------------------------------------------------------------------
-- NOTIFY trigger: announce ad rotation edits to running channel agents.
-- ---------------------------------------------------------------------------
-- The channel broadcast loop loads the ad rotation into memory on startup
-- and only re-reads when it sees a `LISTEN ads_updated` notification. The
-- trigger fires once per statement (not per row) so a bulk update doesn't
-- thunder-herd the agents into reloading N times.
--
-- LISTEN/NOTIFY is delivered only on direct (non-pooled) connections. A
-- transaction-pool-mode pooler (e.g. Neon's pgbouncer) does not relay
-- notifications, so an agent that wants live hot-reload must use the direct
-- connection string. On the pooler the trigger is harmless (NOTIFY is a
-- no-op when no one is listening) and the agent runs with the snapshot it
-- loaded at boot until the worker process is restarted.
CREATE OR REPLACE FUNCTION notify_ads_updated() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify('ads_updated', '');
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER ads_updated_notify
    AFTER INSERT OR UPDATE OR DELETE ON ads
    FOR EACH STATEMENT EXECUTE FUNCTION notify_ads_updated();

COMMIT;
