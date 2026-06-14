-- Rollback for 014_licensing.sql. Drop license_clients before licenses (FK).
DROP INDEX IF EXISTS idx_audit_entity;
DROP TABLE IF EXISTS audit_log;
DROP TABLE IF EXISTS license_clients;
DROP INDEX IF EXISTS idx_licenses_status;
DROP INDEX IF EXISTS idx_licenses_gallery;
DROP INDEX IF EXISTS idx_licenses_holder;
DROP TABLE IF EXISTS licenses;
