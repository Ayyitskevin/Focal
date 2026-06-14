-- 019_client_market.sql — per-client home market for usage-license pricing.
-- A client operates in one market (Asheville is primary; Charlotte and Raleigh
-- are travel markets). The license-fee SUGGESTION reads the holder client's
-- market to pick the base rate card; the usage multipliers are market-independent
-- doctrine. Advisory only — the committed invoices/licenses fee_cents is untouched.
-- Existing rows default to 'asheville' (the only market live until now). No SQL
-- CHECK: the allowed market set lives in app/pricing.py (MARKET_BASE_CENTS), so
-- adding a market later is a code change with no migration.
ALTER TABLE clients ADD COLUMN market TEXT NOT NULL DEFAULT 'asheville';
