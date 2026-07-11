-- Tenant-local native push registrations and durable per-device delivery log.
-- Hosted middleware selects the product DB before bearer authentication.

CREATE TABLE IF NOT EXISTS mobile_push_devices (
    id                         INTEGER PRIMARY KEY,
    session_id                 TEXT REFERENCES api_sessions(id) ON DELETE RESTRICT,
    installation_id_hash       TEXT NOT NULL UNIQUE CHECK (length(installation_id_hash) = 64),
    token_hash                 TEXT NOT NULL CHECK (length(token_hash) = 64),
    token_ciphertext           TEXT,
    token_version              INTEGER NOT NULL DEFAULT 1 CHECK (token_version > 0),
    revision                   INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    environment                TEXT NOT NULL CHECK (environment IN ('sandbox','production')),
    topic                      TEXT NOT NULL CHECK (length(topic) BETWEEN 1 AND 255),
    origin                     TEXT NOT NULL CHECK (length(origin) BETWEEN 1 AND 2048),
    workspace_cache_namespace  TEXT NOT NULL
                               CHECK (length(workspace_cache_namespace) BETWEEN 1 AND 255),
    locale                     TEXT NOT NULL CHECK (length(locale) BETWEEN 1 AND 35),
    app_version                TEXT NOT NULL CHECK (length(app_version) BETWEEN 1 AND 64),
    pref_new_bookings          INTEGER NOT NULL DEFAULT 1 CHECK (pref_new_bookings IN (0,1)),
    pref_booking_changes       INTEGER NOT NULL DEFAULT 1 CHECK (pref_booking_changes IN (0,1)),
    pref_proposal_responses    INTEGER NOT NULL DEFAULT 1
                               CHECK (pref_proposal_responses IN (0,1)),
    pref_payments              INTEGER NOT NULL DEFAULT 1 CHECK (pref_payments IN (0,1)),
    active                     INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    disabled_reason            TEXT CHECK (
                                   disabled_reason IS NULL OR length(disabled_reason) <= 120
                               ),
    registered_at              TEXT NOT NULL DEFAULT (datetime('now')),
    last_registered_at         TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at                 TEXT NOT NULL DEFAULT (datetime('now')),
    disabled_at                TEXT,
    CHECK (
        (active = 1 AND session_id IS NOT NULL AND token_ciphertext IS NOT NULL)
        OR (active = 0 AND token_ciphertext IS NULL)
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mobile_push_devices_session
    ON mobile_push_devices(session_id) WHERE session_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_mobile_push_devices_active_token
    ON mobile_push_devices(environment, topic, token_hash) WHERE active = 1;
CREATE INDEX IF NOT EXISTS idx_mobile_push_devices_active
    ON mobile_push_devices(active, session_id);

CREATE TABLE IF NOT EXISTS mobile_notification_events (
    id          INTEGER PRIMARY KEY,
    public_id   TEXT NOT NULL UNIQUE CHECK (length(public_id) = 36),
    dedupe_key  TEXT NOT NULL UNIQUE CHECK (length(dedupe_key) BETWEEN 1 AND 255),
    category    TEXT NOT NULL CHECK (category IN (
                    'new_bookings','booking_changes','proposal_responses','payments'
                )),
    route       TEXT NOT NULL CHECK (length(route) BETWEEN 1 AND 255 AND route LIKE '/app/%'),
    title       TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 80),
    body        TEXT NOT NULL CHECK (length(body) BETWEEN 1 AND 180),
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at  TEXT NOT NULL DEFAULT (datetime('now','+7 days'))
);
CREATE INDEX IF NOT EXISTS idx_mobile_notification_events_created
    ON mobile_notification_events(created_at);
CREATE INDEX IF NOT EXISTS idx_mobile_notification_events_expires
    ON mobile_notification_events(expires_at);

CREATE TABLE IF NOT EXISTS mobile_notification_deliveries (
    id               INTEGER PRIMARY KEY,
    event_id         INTEGER NOT NULL
                     REFERENCES mobile_notification_events(id) ON DELETE CASCADE,
    device_id        INTEGER NOT NULL REFERENCES mobile_push_devices(id),
    token_hash       TEXT NOT NULL CHECK (length(token_hash) = 64),
    token_version    INTEGER NOT NULL CHECK (token_version > 0),
    apns_id          TEXT NOT NULL UNIQUE CHECK (length(apns_id) = 36),
    queued_job_id    INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    status           TEXT NOT NULL DEFAULT 'queued'
                     CHECK (status IN (
                         'queued','sending','retry','delivered','skipped','failed'
                     )),
    attempts         INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at  TEXT NOT NULL DEFAULT (datetime('now')),
    claim_token      TEXT CHECK (claim_token IS NULL OR length(claim_token) = 36),
    claimed_at       TEXT,
    http_status      INTEGER,
    reason           TEXT CHECK (reason IS NULL OR length(reason) <= 120),
    delivered_at     TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (event_id, device_id),
    CHECK (
        (status = 'sending' AND claim_token IS NOT NULL AND claimed_at IS NOT NULL)
        OR status != 'sending'
    )
);
CREATE INDEX IF NOT EXISTS idx_mobile_notification_deliveries_due
    ON mobile_notification_deliveries(status, next_attempt_at, id);
CREATE INDEX IF NOT EXISTS idx_mobile_notification_deliveries_device
    ON mobile_notification_deliveries(device_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_mobile_notification_deliveries_dispatch
    ON mobile_notification_deliveries(status, next_attempt_at, queued_job_id);
