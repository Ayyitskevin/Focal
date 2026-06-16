-- Booking → Notion Session spine (Phase B cohesion).
-- A confirmed booking of a flagged event type spawns/links a Notion "Sessions"
-- page so the rest of the pipeline (Odysseus preshoot_pack / balance_chaser /
-- digest) attaches to it the same way it does for a hand-entered session.
--
-- creates_notion_session is OPT-IN (default 0): the Sessions spine only gets a
-- node when Kevin flags a real shoot-type event. A forgotten flag fails toward a
-- visible gap (a shoot that never appears) rather than silently seeding a $0
-- session that fires automations against nothing.
--
-- notion_session_id is the idempotency key: once stamped, re-processing the same
-- booking links the existing Session instead of creating a duplicate. It is
-- SEPARATE from notion_page_id (which already holds the standalone Bookings-DB
-- calendar-mirror page — a booking can have both).
ALTER TABLE event_types ADD COLUMN creates_notion_session INTEGER NOT NULL DEFAULT 0;
ALTER TABLE bookings    ADD COLUMN notion_session_id TEXT;
