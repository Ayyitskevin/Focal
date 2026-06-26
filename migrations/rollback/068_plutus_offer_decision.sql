-- Rollback for 068_plutus_offer_decision.sql. Safe: both columns are additive, nullable,
-- and read by nothing but the offers operator surface — dropping them removes the recorded
-- decisions but touches no money, invoice, or offer-summary state. Requires SQLite >= 3.35
-- (ALTER TABLE DROP COLUMN); the deploy target is 3.45+.
ALTER TABLE galleries DROP COLUMN plutus_offer_decided_at;
ALTER TABLE galleries DROP COLUMN plutus_offer_decision;
