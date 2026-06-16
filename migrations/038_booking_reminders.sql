-- Per-booking reminder send tracking. The recurring sweeper sends a T-48h and a
-- T-24h client nudge; these flags make each send idempotent so the loop can fire
-- as often as it likes and a booking still gets at most one of each.
ALTER TABLE bookings ADD COLUMN reminded_48h INTEGER NOT NULL DEFAULT 0;
ALTER TABLE bookings ADD COLUMN reminded_24h INTEGER NOT NULL DEFAULT 0;
