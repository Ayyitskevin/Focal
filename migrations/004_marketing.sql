ALTER TABLE assets ADD COLUMN portfolio INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS inquiries (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    business TEXT,
    message TEXT NOT NULL,
    emailed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
