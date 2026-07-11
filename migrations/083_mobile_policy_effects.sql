-- Durable, leased delivery for Milestone 4B policy-command side effects.
ALTER TABLE mobile_commands ADD COLUMN effects_claimed_at TEXT;
ALTER TABLE mobile_commands ADD COLUMN effects_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE mobile_commands ADD COLUMN effects_last_error TEXT;
