-- 078_license_invoice_link.sql — couple a usage licence to the invoice that granted it.
--
-- A B2B F&B shoot is sold WITH usage rights ("includes a 1-year US social licence"), but the
-- licence and the invoice were unlinked records — the operator re-keyed the grant by hand and
-- nothing tied the rights to the money. This adds the link: a licence can record the invoice it was
-- granted with, so the invoice page can spawn + list its licences and the company view can show
-- "granted with invoice X". Nullable — every existing licence (and any granted outside an invoice)
-- reads NULL and is unaffected.
--
-- It is a LINK only: the licence is still its own record with its own status/term; this never
-- touches the invoice total, the line items, or the money path (§11.4). Additive, forward-only.
ALTER TABLE licenses ADD COLUMN invoice_id INTEGER;

CREATE INDEX IF NOT EXISTS idx_licenses_invoice ON licenses(invoice_id);
