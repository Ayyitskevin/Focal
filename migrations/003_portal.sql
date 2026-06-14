-- Phase 2: per-client content portal — portal auth, brand assets,
-- gallery↔client link, caption hand-off, usage rights.

ALTER TABLE galleries ADD COLUMN client_id INTEGER REFERENCES clients(id);
ALTER TABLE galleries ADD COLUMN captions TEXT;
ALTER TABLE clients ADD COLUMN usage_rights TEXT;

CREATE TABLE IF NOT EXISTS portals (
    id          INTEGER PRIMARY KEY,
    client_id   INTEGER UNIQUE NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    slug        TEXT UNIQUE NOT NULL,
    pin         TEXT NOT NULL,
    published   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS brand_assets (
    id          INTEGER PRIMARY KEY,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    filename    TEXT NOT NULL,
    stored      TEXT NOT NULL,
    bytes       INTEGER,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_brand_assets_client ON brand_assets(client_id);
