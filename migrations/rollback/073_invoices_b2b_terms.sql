-- Rollback for 073_invoices_b2b_terms.sql. Safe: both columns are additive and read only by
-- the invoice surface (admin draft controls + client-facing invoice). Dropping them removes the
-- PO reference and net-terms window but touches no money, payment, or send state — total_cents,
-- deposit_cents, due_date, and status are independent. Requires SQLite >= 3.35 (DROP COLUMN);
-- deploy target is 3.45+.
ALTER TABLE invoices DROP COLUMN po_number;
ALTER TABLE invoices DROP COLUMN net_days;
