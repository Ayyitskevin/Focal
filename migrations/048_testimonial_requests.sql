-- Client self-submit testimonials. Admin creates a request (tokened /t/{slug}
-- link, sent manually); the client writes their own quote, which lands as an
-- unpublished row in the existing `testimonials` table for admin moderation.
-- The existing testimonials table is left untouched (altering its NOT NULL
-- columns would force a live-data table rebuild).
CREATE TABLE IF NOT EXISTS testimonial_requests (
    id             INTEGER PRIMARY KEY,
    slug           TEXT NOT NULL UNIQUE,
    client_id      INTEGER REFERENCES clients(id)      ON DELETE CASCADE,
    project_id     INTEGER REFERENCES projects(id)     ON DELETE SET NULL,
    gallery_id     INTEGER REFERENCES galleries(id)    ON DELETE SET NULL,
    testimonial_id INTEGER REFERENCES testimonials(id) ON DELETE SET NULL,
    submitted_at   TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_testimonial_requests_client
    ON testimonial_requests(client_id);
