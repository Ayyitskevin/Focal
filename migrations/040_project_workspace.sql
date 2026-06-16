-- Unified client-facing project workspace (#1) — one PIN-gated public page per
-- project that aggregates the client's sent docs + delivered gallery into a
-- single hub (read-only; it links out to /p /c /i for the actual accept/sign/pay
-- actions, never re-implements them). Slug + PIN are assigned when Kevin clicks
-- "Publish workspace"; null until then. UNIQUE index (not inline UNIQUE) so the
-- many unpublished rows can share NULL slugs.
ALTER TABLE projects ADD COLUMN workspace_slug TEXT;
ALTER TABLE projects ADD COLUMN workspace_pin TEXT;
ALTER TABLE projects ADD COLUMN workspace_published INTEGER NOT NULL DEFAULT 0;
CREATE UNIQUE INDEX idx_projects_workspace_slug ON projects(workspace_slug);
