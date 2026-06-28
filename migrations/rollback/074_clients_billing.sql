-- Rollback for 074_clients_billing.sql. Safe: all three columns are additive, nullable, and read
-- only by the client edit form + the invoice billing block. Dropping them removes the billing
-- contact / address / tax-id but touches no project, invoice, payment, or license state. Requires
-- SQLite >= 3.35 (DROP COLUMN); deploy target is 3.45+.
ALTER TABLE clients DROP COLUMN billing_email;
ALTER TABLE clients DROP COLUMN billing_address;
ALTER TABLE clients DROP COLUMN tax_id;
