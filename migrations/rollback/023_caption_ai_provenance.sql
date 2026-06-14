-- Rollback for 023. Manual/emergency only (not globbed by db.migrate). Each added
-- column is self-contained with a constant/NULL default, so the naive DROP COLUMN
-- succeeds on sqlite 3.45+ (reverse order of the adds). Does NOT delete the
-- schema_migrations row (consistent with prior rollbacks). NOTE: dropping
-- ai_draft_original destroys retained AI drafts — only run if you mean to.
ALTER TABLE retainer_captions DROP COLUMN ai_draft_original;
ALTER TABLE retainer_captions DROP COLUMN ai_drafted_at;
ALTER TABLE retainer_captions DROP COLUMN ai_model;
ALTER TABLE retainer_captions DROP COLUMN ai_drafted;
