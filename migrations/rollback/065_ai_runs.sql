-- Rollback for 065_ai_runs.sql. Removes the provenance ledger; it touches no business
-- record (the table is additive and dormant unless a provider-facade flag is armed).
--
-- ORDERING HAZARD — roll back highest-numbered first. Migration 067 adds
-- validation_scores.ai_run_id REFERENCES ai_runs(id). With PRAGMA foreign_keys=ON
-- (app/db.py), dropping ai_runs while validation_scores still exists is permitted by
-- SQLite but leaves a dangling FK: every later write to validation_scores then fails with
-- "no such table: ai_runs" (even for NULL ai_run_id). So if 067 is applied, run
-- rollback/067_validation_set.sql FIRST. As a self-contained safeguard this script drops
-- validation_scores too if it is still present, so running 065's rollback alone cannot
-- brick that table. (album_drafts/066 has no dependency on ai_runs and is untouched.)
DROP TABLE IF EXISTS validation_scores;
DROP INDEX IF EXISTS idx_ai_runs_created;
DROP INDEX IF EXISTS idx_ai_runs_subject;
DROP TABLE IF EXISTS ai_runs;
