-- Durable, tenant-local side effects for a booking reschedule.
--
-- The booking transition and these rows are inserted by the same SQLite
-- transaction. Rows deliberately contain only stable identifiers and bounded
-- dispatch state: no request bodies, contact fields, credentials, or rendered
-- notification content are copied into the outbox.

-- Persist calendar identity before changing its generation scheme. Existing
-- bookings retain the exact legacy UID their clients already received; new
-- bookings get a tenant-scoped UID at insert time.
ALTER TABLE bookings ADD COLUMN calendar_uid TEXT;

UPDATE bookings
   SET calendar_uid = 'mise-booking-' || id || '@kleephotography.com'
 WHERE calendar_uid IS NULL;

CREATE UNIQUE INDEX idx_bookings_calendar_uid
    ON bookings(calendar_uid)
    WHERE calendar_uid IS NOT NULL;

CREATE TABLE booking_workflow_effects (
    id                     INTEGER PRIMARY KEY,
    workflow_id            TEXT NOT NULL
                           CHECK (length(workflow_id) BETWEEN 1 AND 128),
    source_booking_id      INTEGER NOT NULL
                           REFERENCES bookings(id) ON DELETE RESTRICT,
    replacement_booking_id INTEGER NOT NULL
                           REFERENCES bookings(id) ON DELETE RESTRICT,
    effect_kind            TEXT NOT NULL CHECK (effect_kind IN (
                               'client_cancel_ics',
                               'client_request_ics',
                               'studio_reschedule_notice',
                               'notion_booking_patch',
                               'notion_session_link',
                               'google_calendar_move'
                           )),
    sequence_no            INTEGER NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
                               'pending', 'running', 'retry',
                               'succeeded', 'skipped', 'blocked'
                           )),
    attempts               INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at        INTEGER,
    lease_token            TEXT CHECK (
                               lease_token IS NULL OR length(lease_token) = 32
                           ),
    lease_expires_at       INTEGER,
    provider_ref           TEXT CHECK (
                               provider_ref IS NULL OR length(provider_ref) <= 255
                           ),
    error_class            TEXT CHECK (
                               error_class IS NULL OR length(error_class) <= 96
                           ),
    error_code             TEXT CHECK (
                               error_code IS NULL OR length(error_code) <= 96
                           ),
    created_at             INTEGER NOT NULL DEFAULT (unixepoch()),
    updated_at             INTEGER NOT NULL DEFAULT (unixepoch()),
    completed_at           INTEGER,
    UNIQUE (workflow_id, effect_kind),
    UNIQUE (workflow_id, sequence_no),
    UNIQUE (source_booking_id, replacement_booking_id, effect_kind),
    CHECK (source_booking_id <> replacement_booking_id),
    CHECK (
        (effect_kind = 'client_cancel_ics'        AND sequence_no = 10) OR
        (effect_kind = 'client_request_ics'       AND sequence_no = 20) OR
        (effect_kind = 'studio_reschedule_notice' AND sequence_no = 30) OR
        (effect_kind = 'notion_booking_patch'     AND sequence_no = 40) OR
        (effect_kind = 'notion_session_link'      AND sequence_no = 50) OR
        (effect_kind = 'google_calendar_move'     AND sequence_no = 60)
    ),
    CHECK (
        (status IN ('pending', 'retry')
         AND next_attempt_at IS NOT NULL
         AND lease_token IS NULL
         AND lease_expires_at IS NULL
         AND completed_at IS NULL)
        OR
        (status = 'running'
         AND next_attempt_at IS NULL
         AND lease_token IS NOT NULL
         AND lease_expires_at IS NOT NULL
         AND completed_at IS NULL)
        OR
        (status IN ('succeeded', 'skipped', 'blocked')
         AND next_attempt_at IS NULL
         AND lease_token IS NULL
         AND lease_expires_at IS NULL
         AND completed_at IS NOT NULL)
    )
);

CREATE INDEX idx_booking_workflow_effects_workflow
    ON booking_workflow_effects(workflow_id, sequence_no);

CREATE INDEX idx_booking_workflow_effects_due
    ON booking_workflow_effects(status, next_attempt_at, lease_expires_at, sequence_no, id);
