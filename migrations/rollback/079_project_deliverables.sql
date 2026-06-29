-- Rollback for 079_project_deliverables.sql. Drops the deliverable-spec table + its index. Safe:
-- a net-new local table read only by the project page / company view; no money, invoice, licence,
-- or gallery state depends on it. Index then table (FK-free).
DROP INDEX IF EXISTS idx_project_deliverables_project;
DROP TABLE IF EXISTS project_deliverables;
