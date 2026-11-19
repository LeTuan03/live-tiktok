-- What the shop wants said about a product ("Nội dung cần giới thiệu"):
-- promotions, gifts, guarantees. The promo script treats it as product facts,
-- so the host may mention it but never invents anything beyond it.
BEGIN;

ALTER TABLE products
    ADD COLUMN talking_points TEXT NOT NULL DEFAULT '';

COMMIT;
