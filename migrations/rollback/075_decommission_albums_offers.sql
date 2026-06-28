-- Rollback for 075_decommission_albums_offers.sql. Recreates the albums tables (066 + 070 columns)
-- and the 13 plutus_* gallery columns (055/059/062/068/069/072) as they stood before the cut.
-- Schema only — any prior album/offer DATA is gone (it was dormant, so there is none to restore).
-- Restores the structures only so a re-introduction (or a forensic rollback) has the same shape.

CREATE TABLE IF NOT EXISTS album_drafts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    gallery_id    INTEGER NOT NULL REFERENCES galleries(id) ON DELETE CASCADE,
    status        TEXT NOT NULL DEFAULT 'draft'
                  CHECK (status IN ('draft','approved','rejected')),
    provider      TEXT,
    model         TEXT,
    spread_count  INTEGER NOT NULL DEFAULT 0,
    notes         TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT,
    ordered_at    TEXT,
    order_size    TEXT,
    order_cover   TEXT,
    order_notes   TEXT
);
CREATE INDEX IF NOT EXISTS idx_album_drafts_gallery ON album_drafts(gallery_id, status);

CREATE TABLE IF NOT EXISTS album_placements (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    album_draft_id  INTEGER NOT NULL REFERENCES album_drafts(id) ON DELETE CASCADE,
    asset_id        INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    spread          INTEGER NOT NULL DEFAULT 0,
    slot            INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (album_draft_id, asset_id)
);
CREATE INDEX IF NOT EXISTS idx_album_placements_draft
    ON album_placements(album_draft_id, spread, slot);

ALTER TABLE galleries ADD COLUMN plutus_last_run_id INTEGER;
ALTER TABLE galleries ADD COLUMN plutus_last_status TEXT;
ALTER TABLE galleries ADD COLUMN plutus_last_error TEXT;
ALTER TABLE galleries ADD COLUMN plutus_last_at TEXT;
ALTER TABLE galleries ADD COLUMN plutus_last_offer_url TEXT;
ALTER TABLE galleries ADD COLUMN plutus_last_pitch_url TEXT;
ALTER TABLE galleries ADD COLUMN plutus_last_bundle_count INTEGER;
ALTER TABLE galleries ADD COLUMN plutus_last_estimated_cents INTEGER;
ALTER TABLE galleries ADD COLUMN plutus_offer_decision TEXT;
ALTER TABLE galleries ADD COLUMN plutus_offer_decided_at TEXT;
ALTER TABLE galleries ADD COLUMN plutus_offer_sent_at TEXT;
ALTER TABLE galleries ADD COLUMN plutus_offer_sent_to TEXT;
ALTER TABLE galleries ADD COLUMN plutus_last_bundles TEXT;
