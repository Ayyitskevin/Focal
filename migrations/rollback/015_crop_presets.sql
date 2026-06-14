-- Rollback for 015_crop_presets.sql. Manual/emergency use only — this dir is
-- not globbed by db.migrate(). Existing on-disk crops are unaffected; consumers
-- must be reverted to imaging.CROP_SIZES in the same change.
DROP INDEX IF EXISTS idx_crop_presets_active;
DROP TABLE IF EXISTS crop_presets;
