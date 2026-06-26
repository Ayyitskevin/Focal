-- 070_album_order.sql — "ordered" state + recorded spec for an approved album draft.
--
-- The albums queue can approve/reject a draft (066) but had nowhere to record that an
-- approved album was actually ORDERED. These additive columns persist that as a record-only
-- step: the operator marks an approved album ordered and captures the spec (size, cover,
-- free-form notes) plus the ordered date. The album's photo list and spread count are the
-- draft's existing placements/spread_count — not re-snapshotted, since an approved draft's
-- layout is fixed (editing means re-proposing).
--
-- 'ordered' is kept SEPARATE from album_drafts.status (which stays draft/approved/rejected,
-- per its CHECK) rather than added as a fourth status — mirroring how Plutus offer decisions
-- and sends are separate columns. Recording an order NEVER prints, hands off to a vendor, or
-- charges anything: it is an internal fulfillment record the operator acts on however they
-- order today (audit §11.4, ADR 0019). Additive and forward-only; existing rows read NULL
-- (not ordered) and behavior is unchanged until the operator marks one.
ALTER TABLE album_drafts ADD COLUMN ordered_at TEXT;
ALTER TABLE album_drafts ADD COLUMN order_size TEXT;
ALTER TABLE album_drafts ADD COLUMN order_cover TEXT;
ALTER TABLE album_drafts ADD COLUMN order_notes TEXT;
