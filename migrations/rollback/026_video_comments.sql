-- Rollback for 026_video_comments.sql. Drop indexes before the table.
DROP INDEX IF EXISTS idx_vcomments_gallery;
DROP INDEX IF EXISTS idx_vcomments_parent;
DROP INDEX IF EXISTS idx_vcomments_asset;
DROP TABLE IF EXISTS video_comments;
