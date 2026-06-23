-- 054_argus_vision.sql — Argus vision hand-off status on galleries (Phase 6).
-- One-way outbound to Argus on publish; Mise records the last run/job for admin surfacing.

ALTER TABLE galleries ADD COLUMN argus_last_run_id INTEGER;
ALTER TABLE galleries ADD COLUMN argus_last_job_id TEXT;
ALTER TABLE galleries ADD COLUMN argus_last_status TEXT;
ALTER TABLE galleries ADD COLUMN argus_last_error TEXT;
ALTER TABLE galleries ADD COLUMN argus_last_at TEXT;