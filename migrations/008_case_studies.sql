-- Marketing-side case studies. A published gallery can be promoted as a public
-- /work/<slug> page with brief + credits + location for SEO; portfolio-starred
-- photos from that gallery become the case-study grid.
ALTER TABLE galleries ADD COLUMN cs_published INTEGER NOT NULL DEFAULT 0;
ALTER TABLE galleries ADD COLUMN cs_tagline   TEXT;
ALTER TABLE galleries ADD COLUMN cs_brief     TEXT;
ALTER TABLE galleries ADD COLUMN cs_credits   TEXT;   -- free-form, "Chef: X\nStylist: Y"
ALTER TABLE galleries ADD COLUMN cs_location  TEXT;
