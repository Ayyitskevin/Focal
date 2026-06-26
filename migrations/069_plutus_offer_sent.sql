-- 069_plutus_offer_sent.sql — operator "offer sent to client" state for Plutus offers.
--
-- The offers queue records an approve/reject DECISION (068) but had nowhere to record that
-- the approved offer was actually SENT to the client. These additive columns persist that,
-- so the queue shows "Sent" and the send is a deliberate, logged operator action — mirroring
-- how proposals/contracts/invoices are emailed: a human edits a pre-filled draft and clicks
-- Send, with the send recorded in emails_log.
--
-- Recording a send NEVER charges or creates an invoice — it emails the offer LINK the
-- operator already approved (warm note + link; pricing stays on the offer page). The money
-- path is untouched: acceptance still flows through the existing, human-initiated invoice
-- workflow (audit §11.4). Additive and forward-only; existing rows read NULL (not sent) and
-- behavior is unchanged until the operator sends.
ALTER TABLE galleries ADD COLUMN plutus_offer_sent_at TEXT;
ALTER TABLE galleries ADD COLUMN plutus_offer_sent_to TEXT;
