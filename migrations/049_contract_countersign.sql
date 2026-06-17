-- Countersignature — studio-side typed-name signature recorded alongside the
-- client's, making the signed contract a bilateral record. We keep status='signed'
-- (no CHECK rebuild); a non-NULL countersigned_at means "fully executed by both
-- parties". Same ESIGN basis as the client signature: typed name + timestamp.
ALTER TABLE contracts ADD COLUMN countersigner_name TEXT;
ALTER TABLE contracts ADD COLUMN countersigned_at   TEXT;
