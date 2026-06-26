-- 066_album_drafts.sql — Mnemosyne album foundation (dormant).
--
-- An album draft is a curated, ordered subset of a gallery's photos arranged into
-- spreads — the proposal a (future) Mnemosyne worker emits and a human approves before
-- anything is printed. This migration lands the two tables that back that lifecycle plus
-- a DB-level duplicate guard; NOTHING in the running app reads or writes them yet.
--
-- Safety (audit §11.4, ADR 0009): an album draft is HUMAN_REVIEW state. status starts
-- at 'draft' and only a human transition moves it to 'approved'/'rejected'. The
-- deterministic validator in app/albums.py owns the correctness invariant the audit
-- insists on — never silently omit, duplicate, or misassign a photo — and refuses to
-- persist a draft that violates it; the UNIQUE(album_draft_id, asset_id) below is the
-- belt-and-suspenders DB backstop for the duplicate case.
--
-- Additive and forward-only: two new tables + one index, no change to any existing
-- table or column. Applying it with the feature dormant writes nothing and changes no
-- current behavior.
CREATE TABLE IF NOT EXISTS album_drafts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    gallery_id    INTEGER NOT NULL REFERENCES galleries(id) ON DELETE CASCADE,
    status        TEXT NOT NULL DEFAULT 'draft'
                  CHECK (status IN ('draft','approved','rejected')),
    provider      TEXT,                 -- mnemosyne | mock | ...
    model         TEXT,
    spread_count  INTEGER NOT NULL DEFAULT 0,
    notes         TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_album_drafts_gallery ON album_drafts(gallery_id, status);

CREATE TABLE IF NOT EXISTS album_placements (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    album_draft_id  INTEGER NOT NULL REFERENCES album_drafts(id) ON DELETE CASCADE,
    asset_id        INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    spread          INTEGER NOT NULL DEFAULT 0,    -- page-pair index, 0-based
    slot            INTEGER NOT NULL DEFAULT 0,    -- position within the spread, 0-based
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (album_draft_id, asset_id)             -- no photo placed twice in one draft
);
CREATE INDEX IF NOT EXISTS idx_album_placements_draft
    ON album_placements(album_draft_id, spread, slot);
