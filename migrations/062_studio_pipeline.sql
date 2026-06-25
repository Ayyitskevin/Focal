-- Studio pipeline metadata for Mise admin (Argus vision + Plutus bundles)
ALTER TABLE galleries ADD COLUMN argus_last_review_url TEXT;
ALTER TABLE galleries ADD COLUMN plutus_last_pitch_url TEXT;
ALTER TABLE galleries ADD COLUMN plutus_last_bundle_count INTEGER;
ALTER TABLE galleries ADD COLUMN plutus_last_estimated_cents INTEGER;