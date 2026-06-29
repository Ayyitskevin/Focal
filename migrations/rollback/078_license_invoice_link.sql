-- Rollback for 078_license_invoice_link.sql. Drops the invoice link + its index. Safe: the column
-- is additive and read only by the invoice page / company view; no money, invoice, or licence-term
-- state depends on it. Plain DROP COLUMN (SQLite 3.45+); deploy target is 3.45+.
DROP INDEX IF EXISTS idx_licenses_invoice;
ALTER TABLE licenses DROP COLUMN invoice_id;
