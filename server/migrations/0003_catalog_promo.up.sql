-- Product catalog fields for AI promo videos (Google Flow / Veo).
--
-- The promo flow reads the shop's existing `products` rows (feature image in
-- `product_media` position 0) and needs a few more facts for the script and
-- the Veo prompts. All default to empty so existing rows and readers are
-- unaffected.
BEGIN;

ALTER TABLE products
    ADD COLUMN brand       TEXT NOT NULL DEFAULT '',
    ADD COLUMN category    TEXT NOT NULL DEFAULT '',
    -- One selling point per line.
    ADD COLUMN highlights  TEXT NOT NULL DEFAULT '',
    -- How the product is held or used on camera (apply to cheek, plug in...).
    ADD COLUMN usage_notes TEXT NOT NULL DEFAULT '';

COMMIT;
