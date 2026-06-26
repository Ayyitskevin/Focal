-- Rollback for 071_product_jobs.sql. Safe: the table is additive and dormant — written by
-- nothing but the (unwired) products foundation, and referenced by no money, invoice, or
-- publication state. Dropping it removes the product-job records and nothing else.
DROP TABLE IF EXISTS product_jobs;
