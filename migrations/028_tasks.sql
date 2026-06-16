-- Studio to-dos: lightweight task list for the photographer, optionally
-- pinned to a project. HoneyBook "Tasks" parity (Phase 3). The calendar view
-- reads due_date from here alongside project shoot dates and invoice due dates.
CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY,
    title       TEXT NOT NULL,
    due_date    TEXT,
    done        INTEGER NOT NULL DEFAULT 0,
    project_id  INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    done_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due_date);
CREATE INDEX IF NOT EXISTS idx_tasks_open ON tasks(done, due_date);
