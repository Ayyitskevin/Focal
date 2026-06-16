-- F&B shoot intake captured on the public booking form (shoot event types only).
-- Flows into the auto-created project's notes and the Notion Session so Kevin walks
-- in knowing the venue, dish count, parking, style refs and on-site contact.
ALTER TABLE bookings ADD COLUMN venue_address  TEXT NOT NULL DEFAULT '';
ALTER TABLE bookings ADD COLUMN dish_count     TEXT NOT NULL DEFAULT '';
ALTER TABLE bookings ADD COLUMN parking_notes  TEXT NOT NULL DEFAULT '';
ALTER TABLE bookings ADD COLUMN style_refs     TEXT NOT NULL DEFAULT '';
ALTER TABLE bookings ADD COLUMN onsite_contact TEXT NOT NULL DEFAULT '';
