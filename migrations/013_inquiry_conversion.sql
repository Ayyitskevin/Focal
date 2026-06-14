-- Track when an inquiry was converted into a client (and, for booking-kind
-- inquiries, the spawned project) so the studio inquiries list can fade out
-- the rows Kevin's already actioned. ON DELETE SET NULL keeps the inquiry row
-- even if the spawned client/project is later deleted.
ALTER TABLE inquiries ADD COLUMN converted_at TEXT;
ALTER TABLE inquiries ADD COLUMN converted_client_id INTEGER
    REFERENCES clients(id) ON DELETE SET NULL;
ALTER TABLE inquiries ADD COLUMN converted_project_id INTEGER
    REFERENCES projects(id) ON DELETE SET NULL;
