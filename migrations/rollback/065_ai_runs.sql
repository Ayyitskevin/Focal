-- Rollback for 065_ai_runs.sql. Safe because the table is additive and dormant by
-- default (only written when a provider-facade flag is armed). Dropping it removes the
-- provenance ledger; it does not touch any business record.
DROP INDEX IF EXISTS idx_ai_runs_created;
DROP INDEX IF EXISTS idx_ai_runs_subject;
DROP TABLE IF EXISTS ai_runs;
