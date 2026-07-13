-- Session-bound replay receipts for consequential native API commands.
--
-- The table deliberately stores only hashes and a bounded canonical response:
-- no raw key, request body, booking contact fields, bearer token, or manage
-- token is duplicated. The tenant boundary is the selected product database; session_id
-- adds the authenticated mobile-session boundary. A key is reserved across all
-- command kinds within one session so accidental reuse cannot cross operations.
-- Receipts become unusable at the absolute mobile session lifetime and are
-- physically removed by the indexed recurring cleanup sweep (plus opportunistic
-- cleanup inside later consequential commands).

CREATE TABLE api_idempotency_replays (
    session_id      TEXT NOT NULL
                    REFERENCES api_sessions(id) ON DELETE CASCADE,
    key_hash        TEXT NOT NULL CHECK (length(key_hash) = 64),
    command_kind    TEXT NOT NULL
                    CHECK (length(command_kind) BETWEEN 1 AND 80),
    request_hash    TEXT NOT NULL CHECK (length(request_hash) = 64),
    response_status INTEGER NOT NULL CHECK (response_status BETWEEN 200 AND 599),
    response_json   TEXT NOT NULL
                    CHECK (json_valid(response_json))
                    CHECK (length(response_json) <= 4096),
    created_at      INTEGER NOT NULL DEFAULT (unixepoch()),
    expires_at      INTEGER NOT NULL,
    PRIMARY KEY (session_id, key_hash),
    CHECK (expires_at > created_at)
);

CREATE INDEX idx_api_idempotency_replays_expiry
    ON api_idempotency_replays(expires_at);
