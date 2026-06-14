-- Domain A: client hierarchy. A client may have a parent client
-- (hospitality group -> region -> venue). Self-referential, nullable.
-- ON DELETE RESTRICT: deleting a client with children is refused at the DB
-- level; restructuring happens only through the explicit set-parent route,
-- never as a delete side-effect. Column-level CHECK blocks the A->A self-loop;
-- the A->B->A cycle is blocked in the app (descendants walk before UPDATE).
ALTER TABLE clients ADD COLUMN parent_id INTEGER
    REFERENCES clients(id) ON DELETE RESTRICT
    CHECK (parent_id IS NULL OR parent_id <> id);
CREATE INDEX IF NOT EXISTS idx_clients_parent ON clients(parent_id);
