-- 020_retainer_quota.sql — deliverable quotas for retainer plans (Domain G slice 1).
-- A retainer commits to a monthly content quota (e.g. "20 hero images + 4 reels").
-- `quota` is a labeled-target JSON template on the plan (same shape idiom as
-- line_items); retainer_deliveries is the per-period MANUAL log of what was
-- actually delivered, summed against the quota to show on-track/behind. Advisory
-- tracking only — no invoice/charge effect, no auto-credit from galleries.
ALTER TABLE recurring_plans ADD COLUMN quota TEXT NOT NULL DEFAULT '[]';

CREATE TABLE IF NOT EXISTS retainer_deliveries (
    id          INTEGER PRIMARY KEY,
    plan_id     INTEGER NOT NULL REFERENCES recurring_plans(id) ON DELETE CASCADE,
    period      TEXT NOT NULL,            -- 'YYYY-MM'
    label       TEXT NOT NULL,            -- matches a quota label (free text)
    qty         INTEGER NOT NULL DEFAULT 0,
    note        TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_retainer_deliveries_plan_period
    ON retainer_deliveries(plan_id, period);
