-- Rollback for 019. Manual/emergency only (not globbed by db.migrate). `market`
-- is a self-contained column with a constant default, so the naive DROP COLUMN
-- succeeds on sqlite 3.45+. Does NOT delete the schema_migrations row (consistent
-- with prior rollbacks).
ALTER TABLE clients DROP COLUMN market;
