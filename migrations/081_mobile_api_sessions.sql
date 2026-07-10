-- Native/mobile API session families.
--
-- These rows live in the product database. Hosted requests already select one
-- immutable tenant database before API authentication; tenant_key adds a second
-- binding check (tenant control-plane id plus normalized origin in hosted mode,
-- normalized origin in self-hosted mode). Access and refresh credentials are
-- never stored here -- only their SHA-256 hashes are persisted.

CREATE TABLE IF NOT EXISTS api_sessions (
    id                   TEXT PRIMARY KEY,
    tenant_key           TEXT NOT NULL,
    principal_kind       TEXT NOT NULL
                         CHECK (principal_kind IN (
                             'studio_owner',
                             'gallery_guest',
                             'portal_guest',
                             'workspace_guest',
                             'document_guest'
                         )),
    resource_id          INTEGER,
    resource_variant     TEXT,
    gallery_visitor_id   INTEGER REFERENCES visitors(id) ON DELETE CASCADE,
    scopes_json          TEXT NOT NULL CHECK (json_type(scopes_json) = 'array'),
    credential_fingerprint TEXT NOT NULL CHECK (length(credential_fingerprint) = 64),
    installation_id_hash TEXT CHECK (
                             installation_id_hash IS NULL
                             OR length(installation_id_hash) = 64
                         ),
    device_name          TEXT CHECK (device_name IS NULL OR length(device_name) <= 120),
    device_platform      TEXT CHECK (device_platform IS NULL OR length(device_platform) <= 32),
    device_app_version   TEXT CHECK (
                             device_app_version IS NULL
                             OR length(device_app_version) <= 64
                         ),
    created_at           INTEGER NOT NULL,
    last_seen_at         INTEGER NOT NULL,
    absolute_expires_at  INTEGER NOT NULL,
    revoked_at           INTEGER,
    revoke_reason        TEXT,
    CHECK (last_seen_at >= created_at),
    CHECK (absolute_expires_at > created_at),
    CHECK (
        (principal_kind = 'studio_owner'
         AND resource_id IS NULL
         AND resource_variant IS NULL
         AND gallery_visitor_id IS NULL)
        OR
        (principal_kind = 'gallery_guest'
         AND resource_id IS NOT NULL
         AND resource_variant IS NULL
         AND gallery_visitor_id IS NOT NULL)
        OR
        (principal_kind IN ('portal_guest', 'workspace_guest')
         AND resource_id IS NOT NULL
         AND resource_variant IS NULL
         AND gallery_visitor_id IS NULL)
        OR
        (principal_kind = 'document_guest'
         AND resource_id IS NOT NULL
         AND resource_variant IN ('proposal', 'contract', 'invoice')
         AND gallery_visitor_id IS NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_api_sessions_tenant_principal
    ON api_sessions(tenant_key, principal_kind, revoked_at, created_at);

CREATE TABLE IF NOT EXISTS api_tokens (
    id             INTEGER PRIMARY KEY,
    session_id     TEXT NOT NULL REFERENCES api_sessions(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL CHECK (kind IN ('access', 'refresh')),
    token_hash     TEXT NOT NULL UNIQUE CHECK (length(token_hash) = 64),
    created_at     INTEGER NOT NULL,
    expires_at     INTEGER NOT NULL,
    consumed_at    INTEGER,
    revoked_at     INTEGER,
    replaced_by_id INTEGER REFERENCES api_tokens(id) ON DELETE SET NULL,
    CHECK (expires_at > created_at),
    CHECK (kind = 'refresh' OR (consumed_at IS NULL AND replaced_by_id IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_api_tokens_session_kind
    ON api_tokens(session_id, kind, revoked_at, expires_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_api_tokens_one_live_refresh
    ON api_tokens(session_id)
    WHERE kind='refresh' AND consumed_at IS NULL AND revoked_at IS NULL;
