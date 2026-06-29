-- ============================================================
-- V002 — Finance Ledger database + dedicated user
-- ============================================================
-- Creates the database and user for the financial-ledger-mock
-- Spring Boot microservice. Tables are managed by JPA (ddl-auto: update).
-- ============================================================

CREATE DATABASE IF NOT EXISTS finance_ledger
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'finance'@'%' IDENTIFIED BY 'finance_pass';

GRANT ALL PRIVILEGES ON finance_ledger.* TO 'finance'@'%';

FLUSH PRIVILEGES;

-- Version record (in deepferry database where schema_version lives)
USE deepferry;
INSERT IGNORE INTO schema_version (version, description)
VALUES ('V002', 'Finance Ledger database + finance user for financial-ledger-mock');
