-- Promo videos made with Google Flow (soshop.promo).
--
-- The job state used to live in data/promo/<id>/job.json on the API machine,
-- so a second machine (or a redeploy on an ephemeral disk) lost every video.
-- It now lives here. `state` is the whole soshop.promo.jobs.PromoJob as JSON
-- (product and host snapshots, script, per-shot progress, the media-store keys
-- of the clips and final files); `status` and `product_id` are copied out of
-- it for listing. The files themselves are in the media store under
-- promo/<id>/ (soshop_server.storage).
--
-- Existing job.json files are imported with
--     uv run python scripts/migrate_media.py
BEGIN;

CREATE TABLE promo_videos (
    id          TEXT PRIMARY KEY CHECK (id ~ '^[A-Za-z0-9]{1,64}$'),
    product_id  TEXT,
    status      TEXT NOT NULL
                    CHECK (status IN ('draft', 'queued', 'rendering', 'assembling',
                                      'ready', 'failed')),
    state       JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX promo_videos_created_idx ON promo_videos (created_at DESC);
CREATE INDEX promo_videos_product_idx ON promo_videos (product_id);

COMMIT;
