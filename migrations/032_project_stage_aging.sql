-- Track when a project last changed pipeline stage, so the board and the
-- reports funnel can show true time-in-stage (not just age since creation).
-- Nullable + backfilled to created_at; reads COALESCE(stage_changed_at,
-- created_at) so brand-new projects (NULL until their first advance) correctly
-- read as "entered inquiry_received at creation". SQLite forbids a non-constant
-- DEFAULT on ALTER ADD COLUMN, hence the backfill UPDATE instead of a default.
ALTER TABLE projects ADD COLUMN stage_changed_at TEXT;
UPDATE projects SET stage_changed_at = created_at WHERE stage_changed_at IS NULL;
