-- Rollback for 027_shot_list.sql. Drop index before the table.
DROP INDEX IF EXISTS idx_shot_list_project;
DROP TABLE IF EXISTS shot_list;
