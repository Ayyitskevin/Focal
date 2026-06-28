-- Rollback for 072_plutus_offer_bundles.sql. Safe: the column is additive, nullable, and read
-- only by the offers/scorecard surface — dropping it removes the persisted bundle catalogue but
-- touches no money, invoice, or offer-summary state (the plutus_last_bundle_count / _estimated_
-- cents summary columns are independent). Requires SQLite >= 3.35 (ALTER TABLE DROP COLUMN); the
-- deploy target is 3.45+.
ALTER TABLE galleries DROP COLUMN plutus_last_bundles;
