-- Migration 0001 (down): drop the initial schema in reverse-dependency order.
--
-- Run via:
--   psql "$DATABASE_URL" -f migrations/0001_initial_schema.down.sql
--
-- Tables are dropped child-first so foreign keys never block a drop.

BEGIN;

DROP TRIGGER IF EXISTS ads_updated_notify ON ads;
DROP FUNCTION IF EXISTS notify_ads_updated();

DROP TABLE IF EXISTS channel_ad_log;
DROP TABLE IF EXISTS channels;
DROP TABLE IF EXISTS ads;
DROP TABLE IF EXISTS app_state;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS favorites;
DROP TABLE IF EXISTS feed_events;
DROP TABLE IF EXISTS feed_sessions;
DROP TABLE IF EXISTS persona_scripts;
DROP TABLE IF EXISTS personas;
DROP TABLE IF EXISTS campaign_products;
DROP TABLE IF EXISTS campaigns;
DROP TABLE IF EXISTS product_media;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS advertisers;

COMMIT;
