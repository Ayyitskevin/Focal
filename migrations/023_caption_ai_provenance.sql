-- 023_caption_ai_provenance.sql — AI-draft provenance for caption packs (Domain G
-- slice 6b). The FIRST AI-generated content inside Mise. Drafting is an explicit
-- human action ("Draft with AI") that calls Odysseus's caption brain over the
-- mesh; Odysseus owns model selection (Mise does NOT route). An AI draft lands in
-- the editable `body` as a SUGGESTION — it never sets status='approved' and never
-- writes a retainer_deliveries row (generation is severed from delivered-status and
-- from the count; the slice-4/6a assisted-credit path is unchanged).
--
-- Provenance is LOAD-BEARING (this is the training dataset, not an extra):
--   ai_drafted        — was body produced by AI?
--   ai_model          — which model produced it (as reported by Odysseus)
--   ai_drafted_at     — when it was drafted
--   ai_draft_original — the ORIGINAL AI draft, retained UNTOUCHED. `body` is the
--                       human-edited final; this preserves the (draft -> final)
--                       diff so each human edit is a recoverable training pair.
--                       Editing the caption must never overwrite this column.
ALTER TABLE retainer_captions ADD COLUMN ai_drafted INTEGER NOT NULL DEFAULT 0;
ALTER TABLE retainer_captions ADD COLUMN ai_model TEXT;
ALTER TABLE retainer_captions ADD COLUMN ai_drafted_at TEXT;
ALTER TABLE retainer_captions ADD COLUMN ai_draft_original TEXT;
