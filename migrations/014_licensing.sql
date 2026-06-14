-- Domain E: licensing & usage-rights (the F&B moat) + entity-agnostic
-- append-only audit log. Additive only -- no ALTER on existing tables.
-- Money is integer cents (matches 002_studio). Append-only by convention:
-- code never UPDATEs/DELETEs audit_log rows.

CREATE TABLE IF NOT EXISTS licenses (
    id                INTEGER PRIMARY KEY,
    holder_client_id  INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    project_id        INTEGER REFERENCES projects(id)  ON DELETE SET NULL,
    gallery_id        INTEGER REFERENCES galleries(id) ON DELETE SET NULL,
    title             TEXT NOT NULL,
    scope             TEXT NOT NULL DEFAULT '',          -- which dishes/assets, specifics
    coverage_scope    TEXT NOT NULL DEFAULT 'holder_only'
                      CHECK (coverage_scope IN ('holder_only','holder_and_descendants','specific')),
    usage_tier        TEXT NOT NULL DEFAULT 'standard'
                      CHECK (usage_tier IN ('standard','extended','exclusive','unpublished_commercial')),
    exclusivity       TEXT NOT NULL DEFAULT 'non_exclusive'
                      CHECK (exclusivity IN ('non_exclusive','exclusive')),
    territory         TEXT NOT NULL DEFAULT '[]',         -- JSON array, e.g. ["US"] / ["worldwide"]
    channels          TEXT NOT NULL DEFAULT '[]',         -- JSON array from CHANNELS vocab
    published         INTEGER NOT NULL DEFAULT 0,         -- real-world state; 0=unpublished (rate driver)
    fee_cents         INTEGER NOT NULL DEFAULT 0,         -- licensing portion of the fee
    starts_on         TEXT,                               -- term start (date)
    ends_on           TEXT,                               -- NULL = perpetual
    perpetual         INTEGER NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'draft'
                      CHECK (status IN ('draft','active','expired','renewed','terminated')),
    notes             TEXT,
    deleted_at        TEXT,                               -- soft-delete
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_licenses_holder  ON licenses(holder_client_id);
CREATE INDEX IF NOT EXISTS idx_licenses_gallery ON licenses(gallery_id);
CREATE INDEX IF NOT EXISTS idx_licenses_status  ON licenses(status);

-- Coverage for the 'specific' case: explicit client rows a license also covers.
-- Only 'holder_only' is meaningful until Domain A (group/venue hierarchy) lands;
-- this table makes the model hierarchy-ready now so A needs zero ALTER on licenses.
CREATE TABLE IF NOT EXISTS license_clients (
    license_id INTEGER NOT NULL REFERENCES licenses(id) ON DELETE CASCADE,
    client_id  INTEGER NOT NULL REFERENCES clients(id)  ON DELETE CASCADE,
    PRIMARY KEY (license_id, client_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id           INTEGER PRIMARY KEY,
    entity_type  TEXT NOT NULL,            -- 'license','invoice','payment','client',...
    entity_id    INTEGER,
    action       TEXT NOT NULL,            -- 'create','update','status_change','soft_delete'
    actor        TEXT NOT NULL DEFAULT 'admin',
    diff_json    TEXT,                     -- JSON {field:[old,new]} or snapshot
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity_type, entity_id);
