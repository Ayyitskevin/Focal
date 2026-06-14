-- Rollback for 020. Manual/emergency only (not globbed by db.migrate). Drop the
-- log table + index first, then the self-contained quota column (DROP COLUMN
-- works on sqlite 3.45+). Does NOT delete the schema_migrations row (consistent
-- with prior rollbacks).
DROP INDEX IF EXISTS idx_retainer_deliveries_plan_period;
DROP TABLE IF EXISTS retainer_deliveries;
ALTER TABLE recurring_plans DROP COLUMN quota;
