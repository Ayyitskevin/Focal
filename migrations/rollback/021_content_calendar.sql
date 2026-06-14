-- Rollback for 021. Manual/emergency only (not globbed by db.migrate). Drop the
-- index then the self-contained table. Does NOT delete the schema_migrations row
-- (consistent with prior rollbacks).
DROP INDEX IF EXISTS idx_content_calendar_plan_date;
DROP TABLE IF EXISTS content_calendar;
