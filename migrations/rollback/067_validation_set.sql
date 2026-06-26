-- Rollback for 067_validation_set.sql. Safe because both tables are additive and dormant
-- (the harness only holds curated validation cases + human scores; nothing in the running
-- app depends on them and no business record references them). Dropping them removes the
-- validation set and its scores; child scores go with the items.
DROP INDEX IF EXISTS idx_validation_scores_item;
DROP INDEX IF EXISTS idx_validation_items_cap;
DROP TABLE IF EXISTS validation_scores;
DROP TABLE IF EXISTS validation_items;
