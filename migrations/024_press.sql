-- Domain H: press / published-work tracking. The evidenced source of truth for
-- real-world publication that Domain E's licenses.published flag is currently
-- guessed at by hand. Additive only -- NO ALTER on existing tables; no change to
-- licenses/galleries/G tables/audit_log. Reuses the entity-agnostic audit_log
-- (entity_type='press'). Rollback lives in migrations/rollback/024_press.sql
-- (index -> table), never forward-globbed by db.migrate().
--
-- Linkage (all nullable, SET NULL): a press hit may be own-brand/editorial with
-- no client, or may pre-date the gallery it later links to. outlet is the ONLY
-- required anchor. These four FKs are the H->E seam: E joins press to a license
-- on shared gallery/project/covered-client.
--
-- Published-LOG only (no pitch pipeline -> no status column): publish_date IS NULL
-- = pending / not-yet-out; a populated publish_date = published. That field IS the
-- gate E reads (publish_date IS NOT NULL AND publish_date <= today). No bare
-- `published` column -- the word already means three other things in this schema.
--
-- channel is a single value drawn from the shared CHANNELS vocab (app/usage_vocab),
-- validated in app/admin/press.py -- same app-level validation licenses.py uses for
-- its channels (no SQL CHECK, so the vocab can evolve in one Python place). Lets E
-- compute "ran in a channel the license didn't grant" as a real overlap check.

CREATE TABLE IF NOT EXISTS press (
    id            INTEGER PRIMARY KEY,
    -- linkage = the H->E seam (all nullable; mirrors licenses' own linkage)
    client_id     INTEGER REFERENCES clients(id)   ON DELETE SET NULL,
    project_id    INTEGER REFERENCES projects(id)  ON DELETE SET NULL,
    gallery_id    INTEGER REFERENCES galleries(id) ON DELETE SET NULL,
    asset_id      INTEGER REFERENCES assets(id)    ON DELETE SET NULL,
    -- the publication event
    outlet        TEXT NOT NULL,                    -- publication name ("Garden & Gun")
    title         TEXT,                             -- headline / piece title
    url           TEXT,                             -- link to the published piece
    publish_date  TEXT,                             -- 'YYYY-MM-DD'; NULL = pending, set = published (E GATE)
    channel       TEXT,                             -- one of CHANNELS (app-validated); E-overlap feed
    credit        TEXT,                             -- photo-credit / attribution text as it ran
    note          TEXT,
    deleted_at    TEXT,                             -- soft-delete (press is reference data)
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_press_client  ON press(client_id);
CREATE INDEX IF NOT EXISTS idx_press_gallery ON press(gallery_id);
CREATE INDEX IF NOT EXISTS idx_press_date    ON press(publish_date);
