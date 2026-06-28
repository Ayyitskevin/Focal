-- 074_clients_billing.sql — company billing details on clients, for formal B2B invoicing.
--
-- The clients table is person-shaped (name/company/email/phone). To invoice a company cleanly
-- its accounts-payable team needs three more things on the document: a billing contact distinct
-- from the day-to-day contact, the registered billing address, and (when required) a tax/registration
-- number. These are operator-entered, optional, and shown on the invoice only when present:
--
--   billing_email    TEXT — accounts-payable / billing contact email (where formal invoices go;
--                    distinct from clients.email, the working contact). NULL = use the primary.
--   billing_address  TEXT — free-form multi-line billing address block (one field, not six
--                    columns: a solo operator pastes the address their client gives them).
--   tax_id           TEXT — the client company's tax / VAT / registration number, printed on the
--                    invoice when their AP requires it. NULL = omit the line.
--
-- Additive, forward-only, nullable; existing clients read NULL and every invoice renders exactly
-- as before until the operator fills these in. No money, licensing, or coverage state is touched.
ALTER TABLE clients ADD COLUMN billing_email TEXT;
ALTER TABLE clients ADD COLUMN billing_address TEXT;
ALTER TABLE clients ADD COLUMN tax_id TEXT;
