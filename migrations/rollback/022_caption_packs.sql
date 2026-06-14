-- Rollback for 022. Manual/emergency only (not globbed by db.migrate). Drop the
-- index then the self-contained table (index -> table; no column was added).
-- Does NOT delete the schema_migrations row (consistent with prior rollbacks).
DROP INDEX IF EXISTS idx_retainer_captions_plan_period;
DROP TABLE IF EXISTS retainer_captions;
