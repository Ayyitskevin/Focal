-- Rollback for 066_album_drafts.sql. Safe because both tables are additive and dormant
-- by default (nothing in the running app reads or writes them). Dropping them removes the
-- album-draft foundation; it touches no gallery, asset, or money record. Child rows in
-- album_placements go with the table.
DROP INDEX IF EXISTS idx_album_placements_draft;
DROP TABLE IF EXISTS album_placements;
DROP INDEX IF EXISTS idx_album_drafts_gallery;
DROP TABLE IF EXISTS album_drafts;
