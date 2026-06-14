-- Short editorial labels under section headings on the public client gallery,
-- e.g. "Hero dishes from the spring menu". NULL = no caption rendered (silent
-- if empty, same pattern as testimonials).
ALTER TABLE sections ADD COLUMN caption TEXT;
