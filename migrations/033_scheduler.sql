-- Calendly-style scheduler. Mise owns the booking record (the "record" brain).
--
-- Time model: booking instants are stored UTC as 'YYYY-MM-DD HH:MM:SS' (no tz
-- suffix) to match the rest of Mise's datetime('now') columns. Availability is
-- authored in the business-local timezone (config.TIMEZONE, default
-- America/New_York) as minutes-from-local-midnight; the engine (app/scheduling.py)
-- converts local wall-clock to UTC per day so DST is handled by zoneinfo, never
-- by a stored fixed offset.
--
-- CREATE TABLE permits a non-constant DEFAULT (datetime('now')); only ALTER TABLE
-- ADD COLUMN forbids it — that is why bookings.created_at can default here.

CREATE TABLE event_types (
  id                  INTEGER PRIMARY KEY,
  slug                TEXT    NOT NULL UNIQUE,
  name                TEXT    NOT NULL,
  description         TEXT    NOT NULL DEFAULT '',
  duration_min        INTEGER NOT NULL DEFAULT 30,
  location            TEXT    NOT NULL DEFAULT '',   -- "Google Meet" / "Phone" / "On-site" / free text
  color               TEXT    NOT NULL DEFAULT '#b3552e',
  buffer_before_min   INTEGER NOT NULL DEFAULT 0,
  buffer_after_min    INTEGER NOT NULL DEFAULT 0,
  min_notice_hours    INTEGER NOT NULL DEFAULT 12,
  max_per_day         INTEGER NOT NULL DEFAULT 0,    -- 0 = unlimited
  booking_window_days INTEGER NOT NULL DEFAULT 60,
  slot_step_min       INTEGER NOT NULL DEFAULT 0,    -- 0 = step by duration
  active              INTEGER NOT NULL DEFAULT 1,
  position            INTEGER NOT NULL DEFAULT 0,
  created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Weekly recurring availability in business-local minutes-from-midnight.
-- event_type_id NULL = the default schedule (applies to any event type that has
-- no rules of its own). A non-NULL event_type_id overrides the default for that
-- event only.
CREATE TABLE availability_rules (
  id            INTEGER PRIMARY KEY,
  event_type_id INTEGER REFERENCES event_types(id) ON DELETE CASCADE,
  weekday       INTEGER NOT NULL,   -- 0=Mon .. 6=Sun (Python date.weekday())
  start_min     INTEGER NOT NULL,
  end_min       INTEGER NOT NULL
);
CREATE INDEX idx_avail_event ON availability_rules(event_type_id, weekday);

-- Date-specific overrides. available=0 blocks the whole day; available=1 with
-- start_min/end_min sets special hours for that day. event_type_id NULL = global.
CREATE TABLE date_overrides (
  id            INTEGER PRIMARY KEY,
  event_type_id INTEGER REFERENCES event_types(id) ON DELETE CASCADE,
  day           TEXT    NOT NULL,        -- 'YYYY-MM-DD' local
  available     INTEGER NOT NULL DEFAULT 0,
  start_min     INTEGER,
  end_min       INTEGER
);
CREATE INDEX idx_override_day ON date_overrides(day);

CREATE TABLE bookings (
  id              INTEGER PRIMARY KEY,
  token           TEXT    NOT NULL UNIQUE,         -- public reschedule/cancel slug
  event_type_id   INTEGER NOT NULL REFERENCES event_types(id),
  name            TEXT    NOT NULL,
  email           TEXT    NOT NULL,
  phone           TEXT    NOT NULL DEFAULT '',
  notes           TEXT    NOT NULL DEFAULT '',
  start_utc       TEXT    NOT NULL,                -- 'YYYY-MM-DD HH:MM:SS' UTC
  end_utc         TEXT    NOT NULL,
  tz              TEXT    NOT NULL DEFAULT '',      -- visitor tz at booking (display only)
  status          TEXT    NOT NULL DEFAULT 'confirmed',  -- confirmed | cancelled
  cancel_reason   TEXT    NOT NULL DEFAULT '',
  reschedule_of   INTEGER REFERENCES bookings(id),
  inquiry_id      INTEGER,                          -- inquiries row for the Odysseus hook
  google_event_id TEXT,                             -- Phase B (Google Calendar API)
  notion_page_id  TEXT,                             -- one-way Notion writeback page
  created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
  cancelled_at    TEXT
);
CREATE INDEX idx_bookings_start  ON bookings(start_utc);
CREATE INDEX idx_bookings_active ON bookings(event_type_id, status, start_utc);
