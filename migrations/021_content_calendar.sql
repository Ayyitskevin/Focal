-- 021_content_calendar.sql — forward-looking content calendar for retainer plans
-- (Domain G slice 3). Quotas (020) say HOW MANY a retainer owes this month;
-- retainer_deliveries say WHAT LANDED; this table says WHAT'S SCHEDULED FOR WHEN.
-- Each slot is a dated planned item tagged with a label (matching a quota label,
-- free text) that moves planned -> shot -> delivered. Planning only: DECOUPLED
-- from retainer_deliveries — marking a slot 'delivered' does NOT auto-credit the
-- quota count (the delivery log stays the single source of the count, by
-- doctrine). No invoice/charge effect.
CREATE TABLE IF NOT EXISTS content_calendar (
    id          INTEGER PRIMARY KEY,
    plan_id     INTEGER NOT NULL REFERENCES recurring_plans(id) ON DELETE CASCADE,
    slot_date   TEXT NOT NULL,            -- 'YYYY-MM-DD' planned date
    label       TEXT NOT NULL,            -- matches a quota label (free text)
    title       TEXT,                     -- optional: "Spring menu hero — pasta"
    status      TEXT NOT NULL DEFAULT 'planned'
                  CHECK (status IN ('planned','shot','delivered')),
    note        TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_content_calendar_plan_date
    ON content_calendar(plan_id, slot_date);
