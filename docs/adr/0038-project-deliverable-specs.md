# ADR 0038 — Project deliverable specs (the contracted "what we owe" per shoot)

**Status:** Accepted (F&B/commercial spine; builds on ADRs 0027, 0034, 0037)
**Date:** 2026-06-29
**Deciders:** Kevin (owner), principal engineer

## Context

A retainer carries a recurring monthly quota (ADR 0027), but a one-off project shoot had no
structured record of what was contracted — just free-text notes. For a commercial F&B operator,
"this shoot: 25 hero images, 5 reels, 1 social-crop ZIP, CMYK print files" is a real deliverable
spec to track and report progress against. The client/project recon flagged this gap; it complements
the shot list (what to *shoot*) and the licence/invoice coupling (rights + money).

## Decision

A per-project deliverable spec, mirroring the shot-list pattern exactly.

- **Schema (migration 079).** Net-new local table `project_deliverables` (per project, soft-deleted):
  `label`, `spec_qty` (contracted count), `unit` (one of `DELIVERABLE_UNITS` in `usage_vocab`,
  app-validated — no SQL CHECK), `spec_format` (free text — "JPEG sRGB", "CMYK TIFF 300dpi"),
  `delivered_qty` (a **manual** count the operator updates), `sort_order`, `note`. Index on
  `project_id`. Additive; rollback drops the table.
- **CRUD (`app/admin/deliverables.py`).** Create / update / soft-delete, each through `db.tx()` with
  an `audit_log` row (`entity_type='project_deliverable'`) — byte-for-byte the shot-list shape. Routes
  hang off `/admin/studio` and redirect to the owning project; there's no standalone index.
- **Surfaces.** A "Deliverables" panel on the project page (add + per-row edit of delivered/spec,
  unit, format, note, with a ✓ when met), and a delivered/spec roll-up against each active project on
  the company command view (ADR 0034).
- **Operational only.** `delivered_qty` is a manual operator count; nothing here delivers files,
  charges, or sends — it's the studio's own tracking of a commitment (§11.4 untouched).

## Consequences

- The one-off shoot now has the same first-class "what's owed + how much is done" the retainer quota
  gives recurring work — visible on the project and rolled up per company.
- **Low blast radius:** a net-new local table (no FK into money/rights), CRUD mirroring the proven
  shot-list module, soft-delete + audit throughout. Red-light only by virtue of the migration →
  reviewed draft PR; verified up + rollback on a fresh DB.
- Completes a coherent commercial project record: shot list (plan) → deliverable spec (commitment) →
  licence (rights) → invoice (money), all linked and visible from the company view.

## Alternatives considered

- **Reuse the retainer quota table for project deliverables.** Rejected — a retainer quota is a
  *recurring monthly* commitment with per-period snapshots and advisory overage; a project spec is a
  *one-off* contracted list. Conflating them would muddy both models; a dedicated table is the honest
  shape (and mirrors how shot-list stayed its own thing).
- **Derive `delivered_qty` from gallery/asset counts.** Rejected for v1 — a deliverable ("social-crop
  ZIP", "CMYK print files") doesn't map 1:1 to gallery assets; a manual count is honest and simple.
  Auto-derivation can come later behind the same column.
- **Free-text deliverables note only (no structure).** Rejected — that's the status quo the recon
  flagged; structure is what enables the progress roll-up and per-company visibility.
