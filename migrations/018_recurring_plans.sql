-- 018_recurring_plans.sql — recurring retainer plans (the Brand Partner monthly
-- content clients). A plan GENERATES draft invoices on a monthly anchor day; it
-- NEVER sends or charges — the manual-send doctrine is preserved (Kevin clicks
-- Send, Stripe collects). The plan hangs off a project (a retainer is a long-
-- running project), so a generated invoice satisfies invoices.project_id with no
-- synthetic project. last_run_period ('YYYY-MM') dedupes a period to one draft.
CREATE TABLE IF NOT EXISTS recurring_plans (
    id              INTEGER PRIMARY KEY,
    project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title           TEXT NOT NULL,
    line_items      TEXT NOT NULL DEFAULT '[]',   -- JSON template, same shape as invoices
    total_cents     INTEGER NOT NULL DEFAULT 0,
    cadence         TEXT NOT NULL DEFAULT 'monthly' CHECK (cadence IN ('monthly')),
    anchor_day      INTEGER NOT NULL DEFAULT 1 CHECK (anchor_day BETWEEN 1 AND 28),
    active          INTEGER NOT NULL DEFAULT 1,
    last_run_period TEXT,                         -- 'YYYY-MM' already generated (dedupe)
    notes           TEXT,
    deleted_at      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_recurring_project ON recurring_plans(project_id);

-- Trace which plan spawned an invoice (null = one-off invoice, the existing path).
ALTER TABLE invoices ADD COLUMN recurring_plan_id INTEGER
    REFERENCES recurring_plans(id) ON DELETE SET NULL;
