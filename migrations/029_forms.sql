-- Custom forms (Phase 4): a builder for lead-capture forms and client
-- questionnaires. Each form has an unguessable public slug (/forms/{slug}),
-- a kind, and an ordered set of fields. Submissions land in an admin inbox;
-- lead-kind submissions also create an inquiries row + email Kevin so the
-- existing studio Leads pipeline and Odysseus inquiry_intake keep working.
CREATE TABLE IF NOT EXISTS forms (
    id          INTEGER PRIMARY KEY,
    slug        TEXT NOT NULL UNIQUE,
    title       TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'lead'
                  CHECK (kind IN ('lead', 'questionnaire')),
    intro       TEXT,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS form_fields (
    id          INTEGER PRIMARY KEY,
    form_id     INTEGER NOT NULL REFERENCES forms(id) ON DELETE CASCADE,
    label       TEXT NOT NULL,
    ftype       TEXT NOT NULL DEFAULT 'short_text'
                  CHECK (ftype IN ('short_text', 'long_text', 'dropdown',
                                   'date', 'email', 'yesno')),
    required    INTEGER NOT NULL DEFAULT 0,
    options     TEXT,            -- JSON array of choices, dropdown only
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_form_fields_form ON form_fields(form_id, sort_order);

CREATE TABLE IF NOT EXISTS form_submissions (
    id          INTEGER PRIMARY KEY,
    form_id     INTEGER NOT NULL REFERENCES forms(id) ON DELETE CASCADE,
    name        TEXT,
    email       TEXT,
    data        TEXT NOT NULL DEFAULT '{}',   -- JSON: {field_id: answer}
    inquiry_id  INTEGER REFERENCES inquiries(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_form_subs_form ON form_submissions(form_id, created_at);
