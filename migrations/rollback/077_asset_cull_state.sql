-- Rollback for 077_asset_cull_state.sql. Drops the operator cull-decision columns + index. Safe:
-- additive columns read only by the cull surface; originals/derivatives/status/delivery untouched.
-- Plain DROP COLUMN per the rollback/074 idiom (SQLite 3.45+) — no rebuild of the FK-heavy assets table.
DROP INDEX IF EXISTS idx_assets_cull;
ALTER TABLE assets DROP COLUMN cull_source;
ALTER TABLE assets DROP COLUMN cull_decided_at;
ALTER TABLE assets DROP COLUMN cull_state;
