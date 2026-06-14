-- 015_crop_presets.sql — data-driven export/crop preset engine (slice D).
-- Generalizes the 3 hardcoded social crops in imaging.py into pure-data rows.
-- One generic render path consumes any row here; a new channel/format = a new
-- row, not new code. Columns for bleed / CMYK / brand-overlay are carried now
-- (cheap, additive) but only ratio + pixel dims are honored by this slice's
-- render path — print bleed/CMYK and overlay compositing are fast-follow.

CREATE TABLE IF NOT EXISTS crop_presets (
    id              INTEGER PRIMARY KEY,
    slug            TEXT    NOT NULL UNIQUE,           -- filename key + URL token: "1x1"
    name            TEXT    NOT NULL,                  -- human label: "Square (1:1)"
    ratio_label     TEXT    NOT NULL,                  -- "1:1", "4:5", "9:16"
    width           INTEGER NOT NULL,                  -- target px width
    height          INTEGER NOT NULL,                  -- target px height
    centering_x     REAL    NOT NULL DEFAULT 0.5,      -- ImageOps.fit centering
    centering_y     REAL    NOT NULL DEFAULT 0.5,
    bleed_px        INTEGER NOT NULL DEFAULT 0,        -- print bleed margin (fast-follow)
    color_space     TEXT    NOT NULL DEFAULT 'sRGB',   -- 'sRGB' | 'CMYK' (fast-follow)
    dpi             INTEGER NOT NULL DEFAULT 72,        -- print spec metadata (fast-follow)
    target_channel  TEXT,                              -- 'instagram','doordash','menu_print'...
    brand_overlay   INTEGER NOT NULL DEFAULT 0,        -- composite brand kit (fast-follow)
    active          INTEGER NOT NULL DEFAULT 1,
    sort            INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_crop_presets_active ON crop_presets(active, sort);

-- Seed the 3 existing social ratios. slugs MUST match current on-disk crop
-- filenames ({stem}_{slug}.jpg) and portal crop URLs so existing crops/zips
-- and the social-crops feature keep working with zero re-render.
INSERT OR IGNORE INTO crop_presets
    (slug, name, ratio_label, width, height, target_channel, sort)
VALUES
    ('1x1',  'Square (1:1)',   '1:1',  1080, 1080, 'instagram', 10),
    ('4x5',  'Portrait (4:5)', '4:5',  1080, 1350, 'instagram', 20),
    ('9x16', 'Story (9:16)',   '9:16', 1080, 1920, 'instagram', 30);
