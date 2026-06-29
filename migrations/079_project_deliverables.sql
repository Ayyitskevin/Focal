-- 079_project_deliverables.sql — the contracted deliverable spec for a one-off shoot.
--
-- Retainers carry a recurring monthly quota (migration 020); a one-off project shoot had no
-- structured "what we owe" — just free-text notes. This is that spec: per project, the deliverables
-- the operator committed to ("25 hero images, 5 reels, 1 social-crop ZIP, CMYK print files"), with a
-- delivered count so progress is trackable. It complements the retainer quota (recurring) and pairs
-- with the licence/invoice coupling (rights + money) and the shot list (what to shoot).
--
-- Net-new local table (mirrors shot_list, migration 027): per-project, soft-deleted, audited via the
-- entity-agnostic audit_log (entity_type='project_deliverable'). unit is one of DELIVERABLE_UNITS
-- (app/usage_vocab, app-validated — no SQL CHECK, so the vocab evolves in one Python place).
-- delivered_qty is a MANUAL count the operator updates; nothing here delivers, charges, or sends.
CREATE TABLE IF NOT EXISTS project_deliverables (
    id            INTEGER PRIMARY KEY,
    project_id    INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    label         TEXT NOT NULL,                  -- "Hero images", "Reels", "Social-crop ZIP"
    spec_qty      INTEGER NOT NULL DEFAULT 0,     -- contracted count (0 = unspecified / N/A)
    unit          TEXT NOT NULL DEFAULT 'images', -- one of DELIVERABLE_UNITS (app-validated)
    spec_format   TEXT,                           -- free-text format/spec ("JPEG sRGB", "CMYK TIFF 300dpi")
    delivered_qty INTEGER NOT NULL DEFAULT 0,     -- how many delivered so far (manual)
    sort_order    INTEGER NOT NULL DEFAULT 0,
    note          TEXT,
    deleted_at    TEXT,                           -- soft-delete (deliverable specs are reference data)
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_project_deliverables_project ON project_deliverables(project_id);
