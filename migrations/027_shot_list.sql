-- Domain F (shoot production), slice 1: Mise-local shot lists per project. The
-- menu-driven "what we're shooting" list Kevin builds before a shoot. Additive
-- only -- NO ALTER on existing tables; reuses the entity-agnostic audit_log
-- (entity_type='shot_list'). Rollback lives in migrations/rollback/027_shot_list.sql
-- (index -> table), never forward-globbed by db.migrate().
--
-- LOCAL ONLY for now (Kevin: "Mise owns, local for now"): there is no Notion
-- column and no sync path in this slice. Odysseus' preshoot_pack reads its own
-- Notion shotlist DS; pushing these rows to Notion is a deferred LATER slice, so
-- a from-scratch local table is the smallest shippable unit and carries no Notion
-- coupling it would have to unwind later.
--
-- category is a single value drawn from SHOT_CATEGORIES, priority from
-- SHOT_PRIORITIES (both in app/usage_vocab), validated in app/admin/shotlist.py --
-- same app-level validation press.py uses for its channel (no SQL CHECK, so the
-- vocab evolves in one Python place). category is nullable (an unclassified shot
-- is fine); priority always has a value (defaults to 'want').
--
-- sort_order is the manual display order within a project's list (admin reorders
-- by editing it); ties break by id so insertion order is stable.

CREATE TABLE IF NOT EXISTS shot_list (
    id          INTEGER PRIMARY KEY,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,                 -- the shot ("Plated hero, three-quarter")
    category    TEXT,                          -- one of SHOT_CATEGORIES (app-validated), nullable
    priority    TEXT NOT NULL DEFAULT 'want',  -- one of SHOT_PRIORITIES (app-validated)
    sort_order  INTEGER NOT NULL DEFAULT 0,    -- manual display order within the project
    note        TEXT,
    deleted_at  TEXT,                          -- soft-delete (shot lists are reference data)
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_shot_list_project ON shot_list(project_id);
