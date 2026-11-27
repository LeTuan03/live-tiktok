-- Restreaming broadcasts to live platforms (TikTok Live, Shopee Live, ...).
--
-- A stream destination is one RTMP ingest point the studio pushes to: the
-- server URL and stream key a platform hands out for a live session. The key
-- is a secret; the API never returns it, only its last characters.
--
-- A broadcast output links a broadcast to a destination. While the broadcast
-- is live or paused, the API process pushes it to every enabled output
-- (soshop.restream) and records how that is going in `state` and `detail`,
-- which the control room shows.
BEGIN;

CREATE TABLE stream_destinations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform    TEXT NOT NULL CHECK (platform IN ('tiktok', 'shopee', 'youtube', 'custom')),
    name        TEXT NOT NULL,
    -- rtmp:// or rtmps:// URL, as the platform shows it.
    server_url  TEXT NOT NULL,
    -- Empty when not known yet, or for a custom URL that embeds the key.
    stream_key  TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX stream_destinations_created_idx ON stream_destinations (created_at);

CREATE TABLE broadcast_outputs (
    broadcast_id   UUID NOT NULL REFERENCES broadcasts(id) ON DELETE CASCADE,
    destination_id UUID NOT NULL REFERENCES stream_destinations(id) ON DELETE CASCADE,
    enabled        BOOLEAN NOT NULL DEFAULT TRUE,
    -- Written by the pushing process: 'off' when nothing is pushed,
    -- 'connecting', 'live', 'retrying' after a failure, 'error' when the
    -- destination cannot be used as configured.
    state          TEXT NOT NULL DEFAULT 'off'
                       CHECK (state IN ('off', 'connecting', 'live', 'retrying', 'error')),
    detail         TEXT NOT NULL DEFAULT '',
    state_at       TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (broadcast_id, destination_id)
);

-- "Which outputs should be pushing?" on every pass of the restreamer.
CREATE INDEX broadcast_outputs_enabled_idx ON broadcast_outputs (broadcast_id) WHERE enabled;
CREATE INDEX broadcast_outputs_destination_idx ON broadcast_outputs (destination_id);

COMMIT;
