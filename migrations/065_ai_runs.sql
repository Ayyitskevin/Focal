-- Phase 1: unified AI provenance ledger.
--
-- One append-only row per provider call routed through app/providers (capability x
-- provider) — provider, model, normalized status, review class, latency, cost/tokens
-- when reported, and the subject it relates to. Metadata ONLY: never the AI output
-- payload, never a secret (see providers.ProviderResult.provenance).
--
-- Additive and forward-only: a new table + two indexes, no change to any existing
-- table. The table stays dormant until a capability facade flag is armed
-- (MISE_PROVIDER_FACADE_CONTENT for the caption path); applying this migration with
-- the feature OFF writes nothing and changes no current behavior.
CREATE TABLE IF NOT EXISTS ai_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    capability      TEXT NOT NULL,            -- vision | offers | content
    provider        TEXT NOT NULL,            -- argus | plutus | odysseus | dionysus | mock | ...
    status          TEXT NOT NULL,            -- ok | disabled | provider_error | invalid_response
    review          TEXT NOT NULL,            -- none | human_review | explicit_commit
    model           TEXT,
    latency_ms      INTEGER,
    cost_usd        REAL,
    tokens          INTEGER,
    error           TEXT,
    subject_type    TEXT,                     -- e.g. retainer_caption | gallery | project
    subject_id      INTEGER,
    correlation_id  TEXT,
    idempotency_key TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ai_runs_subject ON ai_runs(subject_type, subject_id);
CREATE INDEX IF NOT EXISTS idx_ai_runs_created ON ai_runs(created_at);
