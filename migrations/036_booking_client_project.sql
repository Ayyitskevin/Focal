-- Booking -> Studio identity link. A confirmed booking find-or-creates its
-- client (always) and, for real-shoot event types, a project; these stamp the
-- booking so the booking, inquiry, client, project and Notion Session all share
-- one identity instead of spawning duplicate leads.
ALTER TABLE bookings ADD COLUMN client_id  INTEGER REFERENCES clients(id);
ALTER TABLE bookings ADD COLUMN project_id INTEGER REFERENCES projects(id);
