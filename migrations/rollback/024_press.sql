-- Rollback for 024_press.sql. Drop indexes before the table.
DROP INDEX IF EXISTS idx_press_date;
DROP INDEX IF EXISTS idx_press_gallery;
DROP INDEX IF EXISTS idx_press_client;
DROP TABLE IF EXISTS press;
