-- Durable idempotency ledger for native write commands. One tenant database is
-- selected before API auth, and session_id binds every key to one authenticated
-- mobile session. The mutation, audit row, and replay response commit together.

CREATE TABLE IF NOT EXISTS mobile_commands (
    id                INTEGER PRIMARY KEY,
    session_id        TEXT NOT NULL REFERENCES api_sessions(id) ON DELETE CASCADE,
    idempotency_key   TEXT NOT NULL,
    operation         TEXT NOT NULL,
    request_sha256    TEXT NOT NULL CHECK (length(request_sha256) = 64),
    status_code       INTEGER NOT NULL CHECK (status_code BETWEEN 200 AND 299),
    response_json     TEXT NOT NULL CHECK (json_valid(response_json)),
    effect_json       TEXT CHECK (effect_json IS NULL OR json_valid(effect_json)),
    effects_completed_at TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (session_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_mobile_commands_created
    ON mobile_commands(created_at);
