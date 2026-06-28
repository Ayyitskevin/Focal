-- 077_asset_cull_state.sql — operator cull decision per asset (keep / cut), the spine of
-- AI-assisted culling. The vision sidecars already score every photo (argus_keeper_score,
-- migration 064); this records what the OPERATOR decides about each frame — a reversible flag,
-- never a delete.
--
-- Why net-new (not reusing existing columns): assets.status is the derivative-pipeline enum
-- (pending/ready/failed) and must not be overloaded; favorites belong to the client visitor (wrong
-- actor); portfolio is publication intent. Cull is the operator's keep/cut decision and needs its
-- own state.
--
--   cull_state      TEXT  — NULL = undecided (the §11.4-safe default; every existing asset reads
--                   NULL and nothing changes), 'keep' or 'cut'. CHECK pins the domain.
--   cull_decided_at TEXT  — when the operator last decided (NULL while undecided).
--   cull_source     TEXT  — provenance of the DECISION: 'manual' today; a future assisted pass
--                   could record the scorer ('argus' / 'qwen3-vl@<host>'). The SCORE itself stays
--                   in argus_keeper_score — source-agnostic, so promoting local Qwen needs no change.
--
-- 'cut' is a soft, reversible flag: it touches no original, derivative, or delivery path here (a
-- delivery gate is a separate, reviewed change). Additive, forward-only; existing rows read NULL.
ALTER TABLE assets ADD COLUMN cull_state TEXT
    CHECK (cull_state IN ('keep', 'cut') OR cull_state IS NULL);
ALTER TABLE assets ADD COLUMN cull_decided_at TEXT;
ALTER TABLE assets ADD COLUMN cull_source TEXT;

CREATE INDEX IF NOT EXISTS idx_assets_cull ON assets(gallery_id, cull_state);
