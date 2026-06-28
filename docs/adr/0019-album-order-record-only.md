# ADR 0019 — Album order: record the spec, don't integrate or charge

**Status:** Accepted (Track B — the second money-path-adjacent action)
**Date:** 2026-06-26
**Deciders:** Kevin (owner), principal engineer

> **Update (2026-06-28):** the record-only order is no longer a fulfillment dead-end — an ordered
> album draft now has a one-click **"Add to invoice"** that creates a draft invoice for the
> gallery's project with an `"Album — <size>"` line, priced $0 for the operator to fill. It's a
> **clean line with NO sku**, deliberately NOT counted as offer-attributed upsell (album orders
> aren't Plutus offers; the scorecard's attribution stays offer→sale — see ADR 0024 note). Still
> never sends, charges, or hands off to a vendor (§11.4); the draft is the operator's to price/send.

## Context

Mnemosyne album drafts can be proposed, reviewed, approved, or rejected (ADRs 0009/0011),
but an *approved* album had no "ordered" step — acting on it happened entirely outside Mise,
so the queue couldn't tell a reviewed album from a finished one. This is the second Track B
slice (after offer send, ADR 0018) and is governed by the same money-path boundary: nothing
an operator does to an AI-originated artifact may print, hand off to a vendor, or charge on
its own.

The owner's decision on scope: **mark ordered + record the spec** — record-only, no external
integration. (A print-ready export and a vendor/lab API integration were the considered
larger options; both deferred.)

## Decision

Add an **order** step to the albums surface that records that an approved album was ordered,
with its spec — and stops there.

- **Record-only.** Marking an album ordered captures `ordered_at` plus a free-form spec
  (`order_size`, `order_cover`, `order_notes`) on the draft (migration 070). The operator
  still places the order with their lab however they do today; Mise records the decision and
  spec for reference. It prints nothing, contacts no vendor, and charges nothing.
- **Approved-only.** `albums.mark_ordered` raises `OrderError` unless the draft is
  `approved`, so a `draft`/`rejected` album can't be ordered. The guard is in the domain
  function, not just the route.
- **Spec = the draft.** The album's photo list and spread count are the draft's existing
  placements / `spread_count` — not re-snapshotted, because an approved draft's layout is
  fixed (changing it means re-proposing). Only the order metadata is new.
- **`ordered` is separate from `status`.** It is its own column set, not a fourth value of
  `album_drafts.status` (whose CHECK stays `draft/approved/rejected`) — mirroring how Plutus
  offer decision and send state are separate columns, and avoiding a CHECK rebuild on a core
  table. `ordered_at` is set once and preserved across spec edits; a `clear_order` undoes a
  mistaken mark.
- **Audited.** The column update and an `audit_log` row (`album_ordered` /
  `album_order_updated` / `album_order_cleared`) commit together in one `db.tx()`.

## Consequences

- **Positive:** the album lifecycle is complete inside Mise (propose → review → approve →
  ordered) without weakening the money guardrail; the queue and detail page now show ordered
  state at a glance, and the audit log records who ordered what and when.
- **Schema:** migration 070 adds four nullable columns to `album_drafts`; additive,
  forward-only, rollback drops them (the draft + placements are untouched). Red-light change,
  shipped as a reviewed PR a human merges.
- **Inert by data, no flag:** the order UI appears only on an approved draft; existing rows
  read NULL (not ordered) and nothing changes until an operator marks one.
- **Honest scope:** this does not produce a print-ready export or place the order with a lab.
  Those remain possible later slices (see Alternatives); today the record is the deliverable.

## Alternatives considered

- **Add `ordered` as a fourth `status` value.** Rejected — rebuilding the `album_drafts`
  CHECK constraint touches a core table for no benefit; a separate column set is cleaner and
  lets an album be both approved and ordered.
- **Generate a print-ready order sheet / spec PDF on order.** Deferred — useful, but more
  build (export rendering) than the record-only step the owner chose for this slice.
- **Integrate with a print lab / vendor API.** Deferred — largest build, vendor-specific, and
  likely money-adjacent (checkout/charge); it would need its own scoping and would never
  auto-charge. Out of scope here.
- **Snapshot the photo list at order time.** Rejected as premature — an approved draft's
  layout is fixed, so the live placements are the spec; a snapshot adds storage for a case
  (post-approval edits) the app doesn't support yet.
