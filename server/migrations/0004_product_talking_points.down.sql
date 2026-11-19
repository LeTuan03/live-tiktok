BEGIN;

ALTER TABLE products
    DROP COLUMN IF EXISTS talking_points;

COMMIT;
