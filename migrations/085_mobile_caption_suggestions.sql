-- Durable, non-mutating caption suggestions for the native owner app.
--
-- Caption revisions are monotonic so a strong If-Match cannot suffer an ABA
-- cycle. Suggestion output is short-lived and bound to the exact owner session
-- that requested it. The provider job may populate a candidate, but only a
-- separate version-checked owner command can copy it into retainer_captions.

ALTER TABLE retainer_captions
    ADD COLUMN revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0);
ALTER TABLE retainer_captions
    ADD COLUMN updated_at TEXT;
ALTER TABLE retainer_captions
    ADD COLUMN identity_token TEXT;
ALTER TABLE retainer_captions
    ADD COLUMN ai_claim_token TEXT;
ALTER TABLE retainer_captions
    ADD COLUMN ai_claimed_at TEXT;
UPDATE retainer_captions
   SET identity_token=lower(hex(randomblob(16)))
 WHERE identity_token IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_retainer_captions_identity
    ON retainer_captions(identity_token);
CREATE UNIQUE INDEX IF NOT EXISTS idx_retainer_captions_ai_claim
    ON retainer_captions(ai_claim_token) WHERE ai_claim_token IS NOT NULL;
CREATE TRIGGER IF NOT EXISTS trg_retainer_captions_identity
AFTER INSERT ON retainer_captions
WHEN NEW.identity_token IS NULL
BEGIN
    UPDATE retainer_captions
       SET identity_token=lower(hex(randomblob(16)))
     WHERE id=NEW.id;
END;

-- Every database gets an immutable identity, independent of its hosted slug or
-- filesystem path. Workers capture it before an outbound call and must match it
-- again in the same transaction as any final write. `offboarding` is a durable
-- tenant-local admission barrier: deletion sets it before revoking sessions and
-- scrubbing transient content, so an already-authenticated request cannot race a
-- new suggestion into the database before it is parked.
CREATE TABLE IF NOT EXISTS mobile_runtime_state (
    singleton         INTEGER PRIMARY KEY CHECK (singleton = 1),
    database_identity TEXT NOT NULL UNIQUE
                      CHECK (
                          length(database_identity) = 32
                          AND database_identity NOT GLOB '*[^0-9a-f]*'
                      ),
    offboarding       INTEGER NOT NULL DEFAULT 0 CHECK (offboarding IN (0,1)),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT OR IGNORE INTO mobile_runtime_state
    (singleton, database_identity, offboarding)
VALUES
    (1, lower(hex(randomblob(16))), 0);

-- Content-free quota/capacity evidence deliberately has no caption/session FK.
-- Deleting a caption may cascade its private operation payload, but it must not
-- erase a paid-attempt count or free a concurrent slot while its worker is alive.
CREATE TABLE IF NOT EXISTS mobile_caption_usage (
    id          TEXT PRIMARY KEY CHECK (length(id) = 36),
    state       TEXT NOT NULL DEFAULT 'active'
                CHECK (state IN ('active','finished')),
    accepted_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at  TEXT NOT NULL,
    finished_at TEXT,
    CHECK (
        (state='active' AND finished_at IS NULL)
        OR (state='finished' AND finished_at IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_mobile_caption_usage_state
    ON mobile_caption_usage(state, expires_at);
CREATE INDEX IF NOT EXISTS idx_mobile_caption_usage_accepted
    ON mobile_caption_usage(accepted_at);

CREATE TABLE IF NOT EXISTS mobile_caption_suggestions (
    id                    TEXT PRIMARY KEY CHECK (length(id) = 36),
    session_id            TEXT
                          REFERENCES api_sessions(id) ON DELETE SET NULL,
    caption_id            INTEGER NOT NULL
                          REFERENCES retainer_captions(id) ON DELETE CASCADE,
    job_id                INTEGER UNIQUE REFERENCES jobs(id) ON DELETE SET NULL,
    base_revision         INTEGER NOT NULL CHECK (base_revision >= 0),
    status                TEXT NOT NULL DEFAULT 'queued'
                          CHECK (status IN (
                              'queued',
                              'running',
                              'ready',
                              'failed',
                              'applied',
                              'expired'
                          )),
    context_json          TEXT CHECK (
                              context_json IS NULL OR json_valid(context_json)
                          ),
    candidate_text        TEXT CHECK (
                              candidate_text IS NULL OR length(candidate_text) <= 10000
                          ),
    provider              TEXT,
    model                 TEXT,
    failure_code          TEXT CHECK (
                              failure_code IS NULL OR failure_code IN (
                                  'disabled',
                                  'provider_error',
                                  'invalid_response',
                                  'session_ended',
                                  'unknown_outcome',
                                  'internal'
                              )
                          ),
    provider_attempted_at TEXT,
    completed_at          TEXT,
    applied_at            TEXT,
    expires_at            TEXT NOT NULL,
    created_at            TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (
        (status = 'ready' AND candidate_text IS NOT NULL AND failure_code IS NULL)
        OR
        (status = 'failed' AND candidate_text IS NULL AND failure_code IS NOT NULL)
        OR
        (status NOT IN ('ready', 'failed') AND candidate_text IS NULL
         AND failure_code IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_mobile_caption_suggestions_session
    ON mobile_caption_suggestions(session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mobile_caption_suggestions_caption
    ON mobile_caption_suggestions(caption_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mobile_caption_suggestions_active
    ON mobile_caption_suggestions(caption_id)
    WHERE status IN ('queued', 'running');

-- Session deletion must remove provider context/candidates without erasing the
-- tenant's daily usage evidence. Revocation performs the same scrub in
-- mobile_auth before the row is deleted.
CREATE TRIGGER IF NOT EXISTS trg_mobile_caption_suggestions_session_delete
BEFORE DELETE ON api_sessions
BEGIN
    UPDATE mobile_caption_usage
       SET state='finished',finished_at=COALESCE(finished_at,datetime('now'))
     WHERE state='active' AND id IN (
         SELECT id FROM mobile_caption_suggestions
          WHERE session_id=OLD.id AND status<>'running'
     );
    UPDATE mobile_caption_suggestions
       SET session_id=NULL,
           status=CASE
               WHEN status IN ('queued','running','ready','failed')
               THEN 'failed'
               ELSE status
           END,
           context_json=NULL,
           candidate_text=NULL,
           provider=NULL,
           model=NULL,
           failure_code=CASE
               WHEN status IN ('queued','running','ready','failed')
               THEN 'session_ended'
               ELSE NULL
           END,
           completed_at=CASE
               WHEN status IN ('queued','running','ready','failed')
               THEN COALESCE(completed_at, datetime('now'))
               ELSE completed_at
           END
     WHERE session_id=OLD.id;
END;
