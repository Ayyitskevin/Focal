-- Studio: the HoneyBook side — clients, projects, proposals, contracts,
-- invoices, payments, email log. Money is integer cents throughout.

CREATE TABLE IF NOT EXISTS clients (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    company     TEXT,
    email       TEXT,
    phone       TEXT,
    notes       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS projects (
    id              INTEGER PRIMARY KEY,
    client_id       INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    title           TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'lead'
                    CHECK (status IN ('lead','proposal','contract','invoice',
                                      'shooting','delivered','archived')),
    gallery_id      INTEGER REFERENCES galleries(id) ON DELETE SET NULL,
    notion_page_id  TEXT,
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_projects_client ON projects(client_id);

CREATE TABLE IF NOT EXISTS proposals (
    id          INTEGER PRIMARY KEY,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    slug        TEXT UNIQUE NOT NULL,
    title       TEXT NOT NULL,
    intro       TEXT,
    line_items  TEXT NOT NULL DEFAULT '[]',   -- JSON [{label, qty, unit_cents}]
    total_cents INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft','sent','viewed','accepted','declined')),
    sent_at     TEXT,
    viewed_at   TEXT,
    accepted_at TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_proposals_project ON proposals(project_id);

CREATE TABLE IF NOT EXISTS contracts (
    id          INTEGER PRIMARY KEY,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    slug        TEXT UNIQUE NOT NULL,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,                -- snapshot, merge fields resolved
    body_sha256 TEXT,                         -- locked at send
    status      TEXT NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft','sent','viewed','signed')),
    signer_name TEXT,
    signer_ip   TEXT,
    signed_at   TEXT,
    sent_at     TEXT,
    viewed_at   TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_contracts_project ON contracts(project_id);

CREATE TABLE IF NOT EXISTS invoices (
    id              INTEGER PRIMARY KEY,
    project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    slug            TEXT UNIQUE NOT NULL,
    title           TEXT NOT NULL,
    line_items      TEXT NOT NULL DEFAULT '[]',
    total_cents     INTEGER NOT NULL DEFAULT 0,
    deposit_cents   INTEGER NOT NULL DEFAULT 0,  -- 0 = no deposit split
    due_date        TEXT,
    status          TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft','sent','viewed','deposit_paid','paid')),
    stripe_session_id TEXT,                      -- most recent checkout session
    sent_at         TEXT,
    viewed_at       TEXT,
    paid_at         TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_invoices_project ON invoices(project_id);

CREATE TABLE IF NOT EXISTS payments (
    id              INTEGER PRIMARY KEY,
    invoice_id      INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    stripe_event_id TEXT UNIQUE,                 -- webhook idempotency
    stripe_session_id TEXT,
    amount_cents    INTEGER NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('deposit','balance','full')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_payments_invoice ON payments(invoice_id);

CREATE TABLE IF NOT EXISTS emails_log (
    id          INTEGER PRIMARY KEY,
    project_id  INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    doc_kind    TEXT NOT NULL CHECK (doc_kind IN ('proposal','contract','invoice','other')),
    doc_id      INTEGER,
    to_email    TEXT NOT NULL,
    subject     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_emails_project ON emails_log(project_id);
