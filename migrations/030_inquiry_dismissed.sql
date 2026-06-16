-- Soft-archive for inquiries: dismissed leads are kept (recoverable via undo)
-- but drop out of the active leads list, the home 'new inquiries' count, and
-- the reports lead/conversion metrics.
ALTER TABLE inquiries ADD COLUMN dismissed_at TEXT;
