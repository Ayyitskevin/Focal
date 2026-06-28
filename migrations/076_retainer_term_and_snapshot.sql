-- 076_retainer_term_and_snapshot.sql — retainer lifecycle (term/renewal) + a per-period quota
-- snapshot, the spine of the retainer deepening (the operator's #1 mode is recurring clients).
--
-- recurring_plans gains four nullable/defaulted columns (all additive; existing plans read
-- NULL/0 and behave EXACTLY as today — evergreen, generate-forever):
--   term_start      TEXT  — informational retainer start date ('YYYY-MM-DD').
--   renews_on       TEXT  — term-end / next-decision date ('YYYY-MM-DD'). NULL = evergreen.
--   nudged_renewal  INTEGER NOT NULL DEFAULT 0 — one-shot renewal-reminder flag (mirrors
--                   contracts.nudged_unsigned: nudge once, reset only when renews_on changes).
--   pause_at_term   INTEGER NOT NULL DEFAULT 0 — opt-in soft guard: when 1, the unattended sweep
--                   stops generating drafts for periods AFTER the renewal month (the renewal
--                   month itself still bills). Default 0 = generate forever, today's behavior.
--
-- retainer_period_quota freezes the quota committed for a period at the moment that period's
-- first draft is generated, so the advisory overage figure is computed against what was actually
-- committed that month — not against a quota the operator later edits. Written ONCE per period
-- via INSERT OR IGNORE inside the existing generate_for_plan db.tx() (the UNIQUE index is the
-- idempotency guard). Advisory only: NOTHING here sends, charges, or writes an invoice line.
ALTER TABLE recurring_plans ADD COLUMN term_start TEXT;
ALTER TABLE recurring_plans ADD COLUMN renews_on TEXT;
ALTER TABLE recurring_plans ADD COLUMN nudged_renewal INTEGER NOT NULL DEFAULT 0;
ALTER TABLE recurring_plans ADD COLUMN pause_at_term INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS retainer_period_quota (
    id          INTEGER PRIMARY KEY,
    plan_id     INTEGER NOT NULL REFERENCES recurring_plans(id) ON DELETE CASCADE,
    period      TEXT NOT NULL,            -- 'YYYY-MM' the quota was committed for
    quota_json  TEXT NOT NULL,            -- snapshot of recurring_plans.quota at first generate
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_retainer_period_quota_plan_period
    ON retainer_period_quota(plan_id, period);
