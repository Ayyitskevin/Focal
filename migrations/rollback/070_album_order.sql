-- Rollback for 070_album_order.sql. Safe: all four columns are additive, nullable, and read
-- only by the albums operator surface — dropping them removes the recorded order spec/date
-- but touches no money, invoice, or layout state (the draft + its placements are untouched).
-- Requires SQLite >= 3.35 (ALTER TABLE DROP COLUMN); the deploy target is 3.45+.
ALTER TABLE album_drafts DROP COLUMN order_notes;
ALTER TABLE album_drafts DROP COLUMN order_cover;
ALTER TABLE album_drafts DROP COLUMN order_size;
ALTER TABLE album_drafts DROP COLUMN ordered_at;
