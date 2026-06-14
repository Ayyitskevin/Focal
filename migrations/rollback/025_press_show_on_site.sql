-- Rollback for 025_press_show_on_site.sql. SQLite >=3.35 supports DROP COLUMN.
ALTER TABLE press DROP COLUMN show_on_site;
