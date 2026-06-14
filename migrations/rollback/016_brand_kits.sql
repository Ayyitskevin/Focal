-- Rollback for 016_brand_kits.sql. Manual/emergency use only — this dir is not globbed by
-- db.migrate(). On-disk logo files under BRAND_DIR are unaffected; consumers must be reverted
-- to the no-overlay render path in the same change.
DROP INDEX IF EXISTS idx_brand_kits_client;
DROP TABLE IF EXISTS brand_kits;
