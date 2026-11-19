-- Live studio: host-run livestream sessions with an ordered product list.
--
-- A live session binds one AI model (a `personas` row) to an ordered list of
-- products the model presents on air. The agent worker reports its on-air
-- state back into `live_sessions.live_state` so the studio and viewer pages
-- can poll one source of truth.
BEGIN;

ALTER TABLE personas
    ADD COLUMN language TEXT NOT NULL DEFAULT 'en'
        CHECK (language IN ('vi', 'en'));

CREATE TABLE live_sessions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title       TEXT NOT NULL,
    persona_id  UUID REFERENCES personas(id) ON DELETE SET NULL,
    -- Free-form shop context the model can draw on while chatting
    -- (shop name, promotions, shipping policy).
    notes       TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft', 'live', 'ended')),
    room_name   TEXT,
    -- {"mode": ..., "current_product_id": ..., "last_line": ..., "updated_at": ...}
    -- written by the agent while on air; NULL when not live.
    live_state  JSONB,
    started_at  TIMESTAMPTZ,
    ended_at    TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX live_sessions_created_idx ON live_sessions (created_at DESC);

CREATE TABLE live_products (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id   UUID NOT NULL REFERENCES live_sessions(id) ON DELETE CASCADE,
    -- Presentation order within the session. Not unique: reordering rewrites
    -- every row in one transaction and readers always sort by it.
    position     INTEGER NOT NULL CHECK (position >= 0),
    name         TEXT NOT NULL,
    price        NUMERIC(14, 2),
    currency     TEXT NOT NULL DEFAULT 'VND',
    description  TEXT NOT NULL DEFAULT '',
    highlights   TEXT NOT NULL DEFAULT '',
    image_url    TEXT,
    -- Talking points for the model. It rephrases them, it does not read them.
    script       TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'presenting', 'done', 'skipped')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX live_products_session_position_idx
    ON live_products (session_id, position);

COMMIT;
