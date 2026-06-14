-- Rollback for 018. Manual/emergency only (not globbed by db.migrate).
-- Drop the invoices FK column first (its constraint references recurring_plans),
-- then the index, then the table. recurring_plan_id is a self-contained column-
-- level FK so the naive DROP COLUMN succeeds on sqlite 3.45+/3.46+; one-off
-- invoices (recurring_plan_id NULL) are unaffected. Does NOT delete the
-- schema_migrations row (consistent with prior rollbacks).
ALTER TABLE invoices DROP COLUMN recurring_plan_id;
DROP INDEX IF EXISTS idx_recurring_project;
DROP TABLE IF EXISTS recurring_plans;
