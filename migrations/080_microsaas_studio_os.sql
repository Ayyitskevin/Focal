-- Hosted MicroSaaS studio OS layer.
--
-- These tables are product-database local. In hosted mode each tenant already
-- runs against its own SQLite file, so tenant_id does not belong here.

CREATE TABLE IF NOT EXISTS workflow_rules (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    trigger_key TEXT NOT NULL,
    action_key  TEXT NOT NULL DEFAULT 'task'
                CHECK (action_key IN ('task','event')),
    task_title  TEXT NOT NULL,
    delay_days  INTEGER NOT NULL DEFAULT 0,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (trigger_key, action_key, task_title, delay_days)
);
CREATE INDEX IF NOT EXISTS idx_workflow_rules_trigger
    ON workflow_rules(trigger_key, active);

CREATE TABLE IF NOT EXISTS project_events (
    id               INTEGER PRIMARY KEY,
    project_id       INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind             TEXT NOT NULL DEFAULT 'event'
                     CHECK (kind IN ('lead','proposal','contract','invoice',
                                     'gallery','payment','task','note','event')),
    label            TEXT NOT NULL,
    details          TEXT,
    ref_kind         TEXT,
    ref_id           INTEGER,
    due_date         TEXT,
    done_at          TEXT,
    workflow_rule_id INTEGER REFERENCES workflow_rules(id) ON DELETE SET NULL,
    dedupe_key       TEXT UNIQUE,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_project_events_project
    ON project_events(project_id, done_at, due_date, created_at);

CREATE TABLE IF NOT EXISTS packages (
    id          INTEGER PRIMARY KEY,
    slug        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    description TEXT,
    price_cents INTEGER NOT NULL DEFAULT 0,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS package_leads (
    id          INTEGER PRIMARY KEY,
    package_id  INTEGER NOT NULL REFERENCES packages(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    email       TEXT NOT NULL,
    event_date  TEXT,
    message     TEXT,
    inquiry_id  INTEGER REFERENCES inquiries(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_package_leads_package
    ON package_leads(package_id, created_at);

CREATE TABLE IF NOT EXISTS tags (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    color       TEXT NOT NULL DEFAULT '#2f5c45',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS client_tags (
    client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    tag_id    INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (client_id, tag_id)
);

CREATE TABLE IF NOT EXISTS project_custom_fields (
    id          INTEGER PRIMARY KEY,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    field_key   TEXT NOT NULL,
    field_value TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT,
    UNIQUE (project_id, field_key)
);
