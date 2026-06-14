-- 022_caption_packs.sql — caption deliverables for retainer plans (Domain G
-- slice 6a, MANUAL only — no AI/Odysseus yet; that is 6b on top of this).
-- Quotas (020) say HOW MANY a retainer owes; retainer_deliveries say WHAT LANDED;
-- content_calendar (021) says WHAT'S SCHEDULED WHEN; this table stores the CAPTION
-- TEXT for a deliverable, tracked against a quota label. A caption belongs to a
-- plan + period and may optionally reference the calendar slot it captions.
-- DECOUPLED like every other content layer: creating/editing/approving a caption
-- writes NO retainer_deliveries row. Marking a caption 'approved' reuses the
-- slice-4 assisted-credit prefill (label, qty=1, period) — the human still submits
-- the manual delivery log, which stays the single source of the count, by doctrine.
-- No invoice/charge effect. body is human-written in 6a; the AI-draft hook is 6b.
CREATE TABLE IF NOT EXISTS retainer_captions (
    id          INTEGER PRIMARY KEY,
    plan_id     INTEGER NOT NULL REFERENCES recurring_plans(id) ON DELETE CASCADE,
    slot_id     INTEGER REFERENCES content_calendar(id) ON DELETE SET NULL,
    period      TEXT NOT NULL,            -- 'YYYY-MM' the caption is for
    label       TEXT NOT NULL,            -- quota label this caption counts toward
    body        TEXT NOT NULL,            -- the caption text (human-written in 6a)
    status      TEXT NOT NULL DEFAULT 'draft'
                  CHECK (status IN ('draft','approved')),
    note        TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_retainer_captions_plan_period
    ON retainer_captions(plan_id, period);
