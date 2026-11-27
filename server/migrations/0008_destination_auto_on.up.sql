-- A stream destination can be switched on by itself for each livestream
-- session as Start is pressed (set on the studio's "Kênh phát" page, or by
-- TIKTOK_AUTO_ON for the destination from the settings). The restreamer hands
-- every such destination to the session started last and switches it off on
-- the others, since one stream key carries one stream.
BEGIN;

ALTER TABLE stream_destinations
    ADD COLUMN IF NOT EXISTS auto_on BOOLEAN NOT NULL DEFAULT FALSE;

COMMIT;
