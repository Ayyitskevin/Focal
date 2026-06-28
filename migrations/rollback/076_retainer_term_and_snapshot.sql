-- Rollback for 076_retainer_term_and_snapshot.sql. Drops the per-period quota snapshot table and
-- the four retainer-lifecycle columns. Safe: all are additive and read only by the retainer
-- surface; recurring_plans core (line_items/total/quota/anchor/last_run_period) and all generated
-- invoices are untouched. Plain DROP COLUMN per the rollback/073 idiom (SQLite 3.45+).
DROP TABLE IF EXISTS retainer_period_quota;
ALTER TABLE recurring_plans DROP COLUMN pause_at_term;
ALTER TABLE recurring_plans DROP COLUMN nudged_renewal;
ALTER TABLE recurring_plans DROP COLUMN renews_on;
ALTER TABLE recurring_plans DROP COLUMN term_start;
