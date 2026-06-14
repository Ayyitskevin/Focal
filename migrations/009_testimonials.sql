-- Social-proof quotes for the marketing site. General testimonials (gallery_id
-- NULL) render on /, /services. Gallery-scoped ones render on /work/<slug>.
-- Kevin curates from /admin/studio/testimonials.
CREATE TABLE IF NOT EXISTS testimonials (
    id               INTEGER PRIMARY KEY,
    quote            TEXT NOT NULL,
    attribution_name TEXT NOT NULL,
    business         TEXT,
    gallery_id       INTEGER REFERENCES galleries(id) ON DELETE SET NULL,
    position         INTEGER NOT NULL DEFAULT 0,
    published        INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_testimonials_gallery ON testimonials(gallery_id);
