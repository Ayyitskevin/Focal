-- Domain H slice 4: opt-in flag that lifts a published press hit onto the PUBLIC
-- marketing site ("As seen in"). Additive ALTER only -- no other table touched.
--
-- Why a new flag and not "every published row": publish_date already means
-- "this ran in the real world" (the E gate). But press is ALSO logged purely as
-- internal license evidence, and some of it is client-confidential or simply not
-- a portfolio piece. So public visibility is a SEPARATE, explicit human choice:
-- show_on_site=1 AND the publish_date gate (populated + past) both have to hold
-- before a row is rendered to the open internet. Default 0 = nothing leaks until
-- Kevin toggles it. Rollback (DROP COLUMN, SQLite >=3.35) in rollback/025_*.sql.

ALTER TABLE press ADD COLUMN show_on_site INTEGER NOT NULL DEFAULT 0;
