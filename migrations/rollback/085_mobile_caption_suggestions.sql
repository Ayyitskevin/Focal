-- Roll back only the additive native caption-suggestion state.
BEGIN IMMEDIATE;

-- Never erase at-most-once/quota evidence while provider work may still be
-- active or outcome-ambiguous. Operators must disable admission and reconcile
-- these content-free claims before rollback (see the operations runbook).
DROP TABLE IF EXISTS temp._mise_085_rollback_guard;
CREATE TEMP TABLE _mise_085_rollback_guard (
    active_count INTEGER NOT NULL CHECK (active_count = 0)
);
INSERT INTO _mise_085_rollback_guard
SELECT COUNT(*) FROM mobile_caption_usage WHERE state='active';
INSERT INTO _mise_085_rollback_guard
SELECT COUNT(*) FROM retainer_captions WHERE ai_claim_token IS NOT NULL;
DROP TABLE _mise_085_rollback_guard;

DROP TRIGGER IF EXISTS trg_mobile_caption_suggestions_session_delete;
DROP TRIGGER IF EXISTS trg_retainer_captions_identity;
DROP INDEX IF EXISTS idx_mobile_caption_suggestions_active;
DROP INDEX IF EXISTS idx_mobile_caption_suggestions_caption;
DROP INDEX IF EXISTS idx_mobile_caption_suggestions_session;
DROP TABLE IF EXISTS mobile_caption_suggestions;
DROP INDEX IF EXISTS idx_mobile_caption_usage_accepted;
DROP INDEX IF EXISTS idx_mobile_caption_usage_state;
DROP TABLE IF EXISTS mobile_caption_usage;
DROP TABLE IF EXISTS mobile_runtime_state;
DROP INDEX IF EXISTS idx_retainer_captions_ai_claim;
DROP INDEX IF EXISTS idx_retainer_captions_identity;
ALTER TABLE retainer_captions DROP COLUMN ai_claimed_at;
ALTER TABLE retainer_captions DROP COLUMN ai_claim_token;
ALTER TABLE retainer_captions DROP COLUMN identity_token;
ALTER TABLE retainer_captions DROP COLUMN updated_at;
ALTER TABLE retainer_captions DROP COLUMN revision;

-- Unlike older rollback scripts, this migration is explicitly re-applicable.
-- Leaving the marker behind would make `db.migrate()` skip a now-absent table
-- after an incident rollback.
DELETE FROM schema_migrations
 WHERE name='085_mobile_caption_suggestions.sql';

COMMIT;
