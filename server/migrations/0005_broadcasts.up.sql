-- Livestream sessions built from pre-rendered product videos.
--
-- A broadcast is a playlist of finished videos (promo videos made with Google
-- Flow, or uploaded files): an optional intro played once, the product videos
-- in order, repeated until stopped or `loop_count` times, then an optional
-- outro. Nothing ticks on the server. `playhead` records where playback stood
-- at one instant and the position at any later instant is derived from it and
-- the item durations (soshop.broadcast.timeline). Every control action and
-- every playlist edit re-anchors the playhead to "now".
BEGIN;

CREATE TABLE broadcasts (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title       TEXT NOT NULL,
    notes       TEXT NOT NULL DEFAULT '',
    -- 'continuous' repeats the product videos until stopped; 'count' plays
    -- them loop_count times, then the outro, then ends.
    loop_mode   TEXT NOT NULL DEFAULT 'continuous'
                    CHECK (loop_mode IN ('continuous', 'count')),
    loop_count  INTEGER NOT NULL DEFAULT 1 CHECK (loop_count BETWEEN 1 AND 1000),
    status      TEXT NOT NULL DEFAULT 'idle'
                    CHECK (status IN ('idle', 'live', 'paused', 'ended')),
    -- {"phase", "item_id", "index", "cycle", "offset_ms", "at_ms", "playing"},
    -- set while live or paused, NULL otherwise.
    playhead    JSONB,
    started_at  TIMESTAMPTZ,
    ended_at    TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX broadcasts_created_idx ON broadcasts (created_at DESC);

CREATE TABLE broadcast_items (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    broadcast_id UUID NOT NULL REFERENCES broadcasts(id) ON DELETE CASCADE,
    role         TEXT NOT NULL DEFAULT 'product'
                     CHECK (role IN ('intro', 'product', 'outro')),
    -- Play order among the product videos. Not unique: reordering rewrites
    -- every row in one transaction and readers always sort by it.
    position     INTEGER NOT NULL DEFAULT 0 CHECK (position >= 0),
    -- 'promo': a promo video, source_ref is its job id (data/promo/<id>/).
    -- 'upload': an uploaded file, source_ref is its /media/uploads/ URL.
    source       TEXT NOT NULL CHECK (source IN ('promo', 'upload')),
    source_ref   TEXT NOT NULL,
    -- Length of an uploaded file, measured when it was uploaded. Promo videos
    -- report their own length.
    source_duration_s DOUBLE PRECISION,
    product_id   UUID REFERENCES products(id) ON DELETE SET NULL,
    label        TEXT NOT NULL DEFAULT '',
    -- Seconds the item stays on air. NULL plays the video once; a longer slot
    -- repeats the video until the time is used up.
    duration_s   INTEGER CHECK (duration_s BETWEEN 5 AND 3600),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX broadcast_items_order_idx ON broadcast_items (broadcast_id, role, position);
-- At most one intro and one outro per broadcast.
CREATE UNIQUE INDEX broadcast_items_bookend_idx
    ON broadcast_items (broadcast_id, role) WHERE role <> 'product';
-- "Is this promo video used by a broadcast?" before deleting it.
CREATE INDEX broadcast_items_source_idx ON broadcast_items (source, source_ref);

COMMIT;
