-- Dashboard "Needs you today" — dismissible nudges.
-- The Home next-step nudges are DERIVED each render from live data (overdue
-- invoices, stale inquiries, retainers to send, proposal follow-ups, pending
-- testimonials), so they have no stored row to mark "done". To make them
-- checkable, we record a dismissal keyed to the underlying item. A dismissal
-- only suppresses the nudge for the rest of the current local day: the worklist
-- is "needs you TODAY", so it clears as you check things off and returns
-- tomorrow if the condition still holds. No money/legal state lives here.

CREATE TABLE IF NOT EXISTS dismissed_nudges (
    nudge_key    TEXT PRIMARY KEY,                 -- e.g. inv_overdue:42, inq_reply:7
    dismissed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
