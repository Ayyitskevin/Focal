-- Validation-scoring harness: the promotion gate for a shadowed AI provider.
--
-- Phase 2 shadows a challenger (e.g. Qwen3-VL) against the legacy provider and records
-- both to ai_runs. That accumulates comparison rows but no DECISION. These two tables add
-- the missing piece the audit (§9.5) requires before any cutover: a FIXED validation set
-- plus HUMAN quality scores, from which deterministic code computes a parity verdict.
--
-- * validation_items — the fixed set: one curated subject (a gallery/asset) per row, with
--   an optional human-authored ground-truth note. Curated deliberately; UNIQUE keeps a
--   subject from being added twice for the same capability.
-- * validation_scores — one human quality score in [0,1] per (item, model), optionally
--   linked to the exact ai_runs row scored. UNIQUE(item_id, model) so re-scoring updates
--   in place (record_score upserts) rather than double-counting.
--
-- Additive and forward-only: two new tables + indexes, no change to any existing table.
-- Dormant until rows are curated/scored; the promotion report over an empty set is simply
-- "not ready". Nothing here writes business state or promotes anything — promotion stays a
-- human action.
CREATE TABLE IF NOT EXISTS validation_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    capability   TEXT NOT NULL,            -- vision | offers | content | albums
    subject_type TEXT NOT NULL,            -- gallery | asset | ...
    subject_id   INTEGER NOT NULL,
    label        TEXT,                     -- human name for the case
    expected     TEXT,                     -- human-authored ground-truth note (free text)
    notes        TEXT,
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (capability, subject_type, subject_id)
);

CREATE TABLE IF NOT EXISTS validation_scores (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id      INTEGER NOT NULL REFERENCES validation_items(id) ON DELETE CASCADE,
    provider     TEXT NOT NULL,            -- argus | qwen | plutus | ...
    model        TEXT NOT NULL,            -- argus | qwen3-vl:32b | ...
    score        REAL NOT NULL,            -- human quality score in [0,1]
    ai_run_id    INTEGER REFERENCES ai_runs(id) ON DELETE SET NULL,
    scored_by    TEXT,
    notes        TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT,
    UNIQUE (item_id, model)
);

CREATE INDEX IF NOT EXISTS idx_validation_items_cap ON validation_items(capability, active);
CREATE INDEX IF NOT EXISTS idx_validation_scores_item ON validation_scores(item_id);
