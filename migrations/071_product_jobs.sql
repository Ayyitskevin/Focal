-- 071_product_jobs.sql — Aphrodite product-image foundation (dormant).
--
-- A product job is a request to render a product-image variant from a source photo, plus its
-- lifecycle: budget-capped generation, human approve/reject, an explicit consent/rights
-- confirmation, and an export gate. This migration lands the one table that backs that
-- lifecycle; NOTHING in the running app writes it yet (see app/products.py + ADR 0021).
--
-- Safety (audit §13.5, ADR 0006): a product render is HUMAN_REVIEW state and NEVER published
-- to a client automatically. status starts at 'draft' and only a human moves it to
-- 'approved'/'rejected'; consent_confirmed must be set by a human before export; exported_at
-- is the export gate (NULL until a human exports). cost_usd is the per-job spend the
-- deterministic budget cap (PRODUCTS_BUDGET_USD) sums against — generation that would exceed
-- the cap is refused in code. No money/invoice/publication state is touched by any of this.
--
-- Additive and forward-only: one new table + one index, no change to any existing table.
-- Applying it with the feature dormant (no render URL, budget 0) writes nothing.
CREATE TABLE IF NOT EXISTS product_jobs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    gallery_id        INTEGER NOT NULL REFERENCES galleries(id) ON DELETE CASCADE,
    source_asset_id   INTEGER REFERENCES assets(id) ON DELETE SET NULL,
    kind              TEXT NOT NULL DEFAULT 'variant',   -- what was requested (free-form for now)
    spec              TEXT,                              -- JSON spec / prompt of the render
    provider          TEXT,
    model             TEXT,
    status            TEXT NOT NULL DEFAULT 'draft'
                      CHECK (status IN ('draft','approved','rejected')),
    output_path       TEXT,                              -- generated file, when a backend makes one
    cost_usd          REAL NOT NULL DEFAULT 0,           -- per-job spend (budget-cap ledger)
    consent_confirmed INTEGER NOT NULL DEFAULT 0,        -- human confirmed rights/consent (§13.5)
    exported_at       TEXT,                              -- export gate: NULL until a human exports
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_product_jobs_gallery ON product_jobs(gallery_id, status);
