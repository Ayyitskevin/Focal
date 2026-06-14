-- Domain C slice 3: timecoded review comments on video deliverables. A client
-- (PIN-gated) and the studio (admin) leave feedback anchored to a playhead
-- position on a video asset; replies thread under a comment. Additive only --
-- NO ALTER on existing tables. Rollback in migrations/rollback/026_video_comments.sql.
--
-- Anchored to asset_id (the video deliverable, the tile's data-id) with CASCADE
-- so feedback dies with its video -- no orphan threads. gallery_id is denormalized
-- (also CASCADE) so the PIN-gated endpoints scope by gallery without joining
-- through assets every time (mirrors downloads/favorites).
--
-- parent_id is a self-FK = threading. NULL = top-level (carries the real playhead
-- timecode); a reply copies its parent's timecode at insert so display/sort needs
-- no join, and CASCADE so hiding/removing a parent takes its replies. (The admin
-- hide path soft-deletes a comment AND its descendants in one recursive UPDATE.)
--
-- visitor_id (SET NULL) links a client comment to the visitor cookie that wrote
-- it; admin comments are author_role='admin', visitor_id NULL. A purged visitor
-- leaves the comment text standing (matches downloads).
--
-- status is the C4-forward hook (review/resolve states). Left app-validated with
-- NO SQL CHECK on purpose so C4 can add states without reshaping this table.
-- deleted_at = admin moderation soft-delete (the one auditable human act here;
-- audit_log entity_type='video_comment'). A client comment is just data, no audit.

CREATE TABLE IF NOT EXISTS video_comments (
    id          INTEGER PRIMARY KEY,
    asset_id    INTEGER NOT NULL REFERENCES assets(id)         ON DELETE CASCADE,
    gallery_id  INTEGER NOT NULL REFERENCES galleries(id)      ON DELETE CASCADE,
    parent_id   INTEGER          REFERENCES video_comments(id) ON DELETE CASCADE,
    visitor_id  INTEGER          REFERENCES visitors(id)       ON DELETE SET NULL,
    author_role TEXT NOT NULL CHECK (author_role IN ('client','admin')),
    timecode    REAL NOT NULL DEFAULT 0,           -- seconds (sub-second ok); reply = parent's timecode
    body        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',       -- C4-forward; app-validated, no CHECK so C4 won't reshape
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),  -- UTC; convert at display (clock rule)
    deleted_at  TEXT                                -- admin moderation soft-delete (audited)
);
CREATE INDEX IF NOT EXISTS idx_vcomments_asset   ON video_comments(asset_id, timecode);
CREATE INDEX IF NOT EXISTS idx_vcomments_parent  ON video_comments(parent_id);
CREATE INDEX IF NOT EXISTS idx_vcomments_gallery ON video_comments(gallery_id);
