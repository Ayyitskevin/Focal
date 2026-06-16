-- Google Calendar OAuth token for the single business account (Phase B).
-- One install = one connected calendar, so a single-row table (CHECK id=1).
-- The refresh token is a secret at rest: it lives here in the mode-protected
-- SQLite DB only, never in .env, ORACLE, logs, or memory. Client id/secret stay
-- in .env; the access token is short-lived and refreshed on demand.
CREATE TABLE IF NOT EXISTS google_oauth (
  id            INTEGER PRIMARY KEY CHECK (id = 1),
  refresh_token TEXT NOT NULL,
  access_token  TEXT,
  access_expiry TEXT,           -- UTC 'YYYY-MM-DD HH:MM:SS', compared lexically
  scope         TEXT,
  connected_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
