-- Proofing mode lets Kevin set a target select count on a section
-- (e.g. "pick 25 of 60"). Client favorites count toward the cap; once at cap,
-- the visitor must unfavor one before adding a new pick. Per-section so a
-- gallery can mix proofing chapters with free-form ones.
ALTER TABLE sections ADD COLUMN proof_target INTEGER;
