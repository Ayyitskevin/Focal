-- 068_plutus_offer_decision.sql — operator approve/reject state for Plutus offers.
--
-- The offers review queue (/admin/offers) reads the one-per-gallery offer summary on
-- galleries.plutus_last_* but had nowhere to record the operator's DECISION, so triage was
-- ephemeral. These two additive columns persist that decision so the queue becomes a real
-- workflow: review -> approve/reject -> act on the approved ones.
--
-- plutus_offer_decision: 'approved' | 'rejected' | NULL (undecided). Enforced in app code
-- (admin/offers._set_decision) rather than a CHECK, matching the plain ADD COLUMN style of
-- the other plutus migrations. A decision records the human's call ONLY — approving an
-- offer still never auto-sends, charges, or creates an invoice (AI-proposed pricing stays a
-- human-approved draft, audit §11.4). Additive and forward-only; existing rows read NULL
-- (undecided) and behavior is unchanged until the operator acts.
ALTER TABLE galleries ADD COLUMN plutus_offer_decision TEXT;
ALTER TABLE galleries ADD COLUMN plutus_offer_decided_at TEXT;
