CREATE TABLE IF NOT EXISTS galleries (
    id            INTEGER PRIMARY KEY,
    slug          TEXT UNIQUE NOT NULL,
    title         TEXT NOT NULL,
    client_name   TEXT,
    pin           TEXT NOT NULL,
    cover_asset_id INTEGER,
    expires_at    TEXT,
    published     INTEGER NOT NULL DEFAULT 0,
    content_rev   INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sections (
    id          INTEGER PRIMARY KEY,
    gallery_id  INTEGER NOT NULL REFERENCES galleries(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    position    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS assets (
    id          INTEGER PRIMARY KEY,
    gallery_id  INTEGER NOT NULL REFERENCES galleries(id) ON DELETE CASCADE,
    section_id  INTEGER REFERENCES sections(id) ON DELETE SET NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('photo','video')),
    filename    TEXT NOT NULL,
    stored      TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','ready','failed')),
    width       INTEGER,
    height      INTEGER,
    duration    REAL,
    bytes       INTEGER,
    position    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_assets_gallery ON assets(gallery_id, section_id, position);

CREATE TABLE IF NOT EXISTS visitors (
    id          INTEGER PRIMARY KEY,
    gallery_id  INTEGER NOT NULL REFERENCES galleries(id) ON DELETE CASCADE,
    token       TEXT UNIQUE NOT NULL,
    email       TEXT,
    first_seen  TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen   TEXT
);
CREATE INDEX IF NOT EXISTS idx_visitors_gallery ON visitors(gallery_id);

CREATE TABLE IF NOT EXISTS favorites (
    visitor_id  INTEGER NOT NULL REFERENCES visitors(id) ON DELETE CASCADE,
    asset_id    INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (visitor_id, asset_id)
);

CREATE TABLE IF NOT EXISTS downloads (
    id          INTEGER PRIMARY KEY,
    gallery_id  INTEGER NOT NULL REFERENCES galleries(id) ON DELETE CASCADE,
    visitor_id  INTEGER REFERENCES visitors(id) ON DELETE SET NULL,
    asset_id    INTEGER REFERENCES assets(id) ON DELETE SET NULL,  -- NULL = full ZIP
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_downloads_gallery ON downloads(gallery_id);

CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,
    payload     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued','running','done','failed')),
    attempts    INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS pin_attempts (
    ip          TEXT NOT NULL,
    gallery_id  INTEGER NOT NULL,
    ts          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pin_attempts ON pin_attempts(ip, gallery_id, ts);
