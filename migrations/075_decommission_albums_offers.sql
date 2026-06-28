-- 075_decommission_albums_offers.sql — remove the Mnemosyne ALBUMS and Plutus OFFERS subsystems.
--
-- Both were consumer/portrait-shaped AI sidecars (print/album upsells, lay-flat album layout) that
-- do not fit a solo B2B food-and-beverage commercial workflow. The operator's clients are
-- companies who receive licensed digital files — not coffee-table books or print-product upsells.
-- The application code, admin surfaces, provider-facade capabilities, and AI-pane tiles for both
-- are removed in the same change; this migration drops the now-orphaned schema.
--
-- DROPPED:
--   * album_drafts + album_placements (066/070) — the album-proposal/order tables. Dormant: no
--     production proposer was ever armed, so these hold no live data. Their indexes drop with them.
--   * 13 plutus_* columns on galleries (055/059/062/068/069/072) — the offer summary, decision,
--     send, and bundle state read/written only by the removed offers/scorecard surfaces. None is
--     indexed, so DROP COLUMN is clean. (SQLite has no DROP COLUMN IF EXISTS; every column is
--     guaranteed present here because its creating migration runs earlier.)
--
-- PRESERVED: galleries.validation_set and all vision/caption/products state are untouched — this
-- removes only ALBUMS/OFFERS. No money, asset, client, or gallery-core column is affected; the
-- invoice/proposal/contract/payment path is independent. Requires SQLite >= 3.35 (DROP COLUMN);
-- deploy target is 3.45+.

DROP TABLE IF EXISTS album_placements;
DROP TABLE IF EXISTS album_drafts;

ALTER TABLE galleries DROP COLUMN plutus_last_run_id;
ALTER TABLE galleries DROP COLUMN plutus_last_status;
ALTER TABLE galleries DROP COLUMN plutus_last_error;
ALTER TABLE galleries DROP COLUMN plutus_last_at;
ALTER TABLE galleries DROP COLUMN plutus_last_offer_url;
ALTER TABLE galleries DROP COLUMN plutus_last_pitch_url;
ALTER TABLE galleries DROP COLUMN plutus_last_bundle_count;
ALTER TABLE galleries DROP COLUMN plutus_last_estimated_cents;
ALTER TABLE galleries DROP COLUMN plutus_offer_decision;
ALTER TABLE galleries DROP COLUMN plutus_offer_decided_at;
ALTER TABLE galleries DROP COLUMN plutus_offer_sent_at;
ALTER TABLE galleries DROP COLUMN plutus_offer_sent_to;
ALTER TABLE galleries DROP COLUMN plutus_last_bundles;
