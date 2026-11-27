BEGIN;

ALTER TABLE stream_destinations DROP COLUMN IF EXISTS auto_on;

COMMIT;
