-- 016_brand_kits.sql — brand-kit overlay compositing (slice 3; fast-follow #1 of Domain D).
-- A client's overlay logo + placement spec. DISTINCT from brand_assets (003), which is a
-- general client file locker (mixed types incl. PDF/EPS/AI/ZIP, served as downloads). A
-- brand_kit is a single composite-ready RASTER logo with placement params, consumed
-- SERVER-SIDE at crop render time and baked into the JPEG — never served standalone to the
-- public, so it introduces no new untrusted-token route.
--
-- Additive + opt-in: a crop_presets row with brand_overlay=0 (all 3 seeded presets), or a
-- client with no active kit, renders byte-for-byte as today. Overlay only fires when BOTH
-- the preset opts in (brand_overlay=1) AND the client has an active kit.

CREATE TABLE IF NOT EXISTS brand_kits (
    id          INTEGER PRIMARY KEY,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    label       TEXT,
    stored      TEXT    NOT NULL,                 -- raster logo on disk: BRAND_DIR/{client_id}/{stored}
    bytes       INTEGER,
    position    TEXT    NOT NULL DEFAULT 'br'     -- 9-grid anchor
                CHECK (position IN ('tl','tc','tr','ml','c','mr','bl','bc','br')),
    opacity     INTEGER NOT NULL DEFAULT 100      -- percent, 0-100
                CHECK (opacity BETWEEN 0 AND 100),
    scale_pct   INTEGER NOT NULL DEFAULT 22       -- logo width as % of crop width
                CHECK (scale_pct BETWEEN 1 AND 100),
    margin_pct  INTEGER NOT NULL DEFAULT 4        -- inset from edge as % of crop width
                CHECK (margin_pct BETWEEN 0 AND 50),
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_brand_kits_client ON brand_kits(client_id, active);
