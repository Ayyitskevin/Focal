-- Optional tag on portfolio-starred assets, shown as filter chips above
-- /portfolio. NULL = untagged (always visible when chips show). Single tag per
-- asset keeps the editing UI tight; rename categories case-insensitively by
-- editing the value Kevin uses everywhere.
ALTER TABLE assets ADD COLUMN portfolio_tag TEXT;
