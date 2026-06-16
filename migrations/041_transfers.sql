-- Transfers — WeTransfer-style file sends, built on the gallery engine.
-- A "transfer" is a gallery row WHERE type='drop': no sections/proofing/cover/
-- portfolio/case-study chrome, a stripped client page, one big "Download all".
-- Reuses gallery auth, upload, derivatives, ZIP, and download tracking wholesale.
--   type        'gallery' (default, unchanged) | 'drop' (a transfer)
--   require_pin  1 = PIN-gated like galleries (default) | 0 = link-only (no PIN
--                prompt; a visitor is auto-minted on first view so downloads
--                still track). Drops are created with require_pin=0 by default.
-- Existing rows backfill to type='gallery', require_pin=1 — no behavior change.
ALTER TABLE galleries ADD COLUMN type TEXT NOT NULL DEFAULT 'gallery';
ALTER TABLE galleries ADD COLUMN require_pin INTEGER NOT NULL DEFAULT 1;
