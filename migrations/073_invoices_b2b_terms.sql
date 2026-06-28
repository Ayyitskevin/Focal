-- 073_invoices_b2b_terms.sql — B2B invoicing essentials on invoices: a purchase-order
-- reference and structured net payment terms.
--
-- A solo commercial/F&B operator invoices COMPANIES, whose accounts-payable systems match
-- payment to a purchase-order number and run on net terms (net-15/30/45/60). Today the PO has
-- nowhere to live and "net 30" can only be typed into the free-form `terms` note, so the due
-- date never reflects it. These two columns make both first-class:
--
--   po_number  TEXT  — the client's PO reference, printed on the invoice for their AP team.
--   net_days   INTEGER NOT NULL DEFAULT 0 — payment window in days. 0 = no net terms (the
--              existing manual `due_date` / "On delivery" behavior is unchanged). When > 0,
--              the send step stamps due_date = sent date + net_days (computed in code, not here).
--
-- Additive, forward-only, nullable/defaulted; existing rows read NULL/0 and behave exactly as
-- before. Recording a PO or net-terms NEVER sends or charges — invoices stay draft until the
-- operator marks them sent (audit §11.4).
ALTER TABLE invoices ADD COLUMN po_number TEXT;
ALTER TABLE invoices ADD COLUMN net_days INTEGER NOT NULL DEFAULT 0;
