-- Rollback for 069_plutus_offer_sent.sql. Safe: both columns are additive, nullable, and
-- read only by the offers operator surface — dropping them removes the recorded send
-- timestamp/recipient but touches no money, invoice, or offer-summary state. The emails_log
-- rows for past sends remain (independent table). Requires SQLite >= 3.35 (ALTER TABLE DROP
-- COLUMN); the deploy target is 3.45+.
ALTER TABLE galleries DROP COLUMN plutus_offer_sent_to;
ALTER TABLE galleries DROP COLUMN plutus_offer_sent_at;
