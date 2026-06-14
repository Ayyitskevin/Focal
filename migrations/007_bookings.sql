-- Booking requests share the inquiries table but carry a date + service.
-- kind='contact' is the existing flow (free-form message); 'booking' is the new
-- /book page with a specific shoot date that lifts straight into a project.
ALTER TABLE inquiries ADD COLUMN kind TEXT NOT NULL DEFAULT 'contact';
ALTER TABLE inquiries ADD COLUMN shoot_date TEXT;
ALTER TABLE inquiries ADD COLUMN service TEXT;
ALTER TABLE projects  ADD COLUMN shoot_date TEXT;
