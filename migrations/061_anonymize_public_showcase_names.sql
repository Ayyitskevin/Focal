-- Remove real or invented restaurant names from seeded public showcase content.
UPDATE galleries
SET client_name='Independent Restaurant',
    cs_location=CASE
        WHEN cs_location IS NULL OR cs_location='' OR cs_location='Asheville, NC'
        THEN 'Western North Carolina' ELSE cs_location END,
    cs_credits=CASE
        WHEN cs_credits IS NULL OR cs_credits='' OR cs_credits LIKE '%Mise Demo%' OR cs_credits LIKE '%Cúrate%'
        THEN 'Client: Independent restaurant
Scope: Menu refresh · brand library
Deliverables: 6 finals · social crop pack
Turnaround: Same-week gallery'
        ELSE cs_credits END
WHERE client_name IN ('Mise Demo', 'Cúrate')
   OR cs_credits LIKE '%Mise Demo%'
   OR cs_credits LIKE '%Cúrate%';

UPDATE testimonials
SET attribution_name=CASE business
        WHEN 'Cúrate' THEN 'Restaurant owner'
        WHEN 'High Five Coffee' THEN 'Marketing lead'
        WHEN 'Bull & Beggar' THEN 'Executive chef'
        ELSE attribution_name END,
    business=CASE business
        WHEN 'Cúrate' THEN 'Independent restaurant'
        WHEN 'High Five Coffee' THEN 'Neighborhood cafe'
        WHEN 'Bull & Beggar' THEN 'Chef-owned dining room'
        ELSE business END
WHERE business IN ('Cúrate', 'High Five Coffee', 'Bull & Beggar');
