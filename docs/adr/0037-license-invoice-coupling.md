# ADR 0037 — Licence ↔ invoice coupling (grant a usage licence with the invoice)

**Status:** Accepted (final slice of the F&B/commercial spine batch; builds on ADRs 0029, 0034)
**Date:** 2026-06-29
**Deciders:** Kevin (owner), principal engineer

## Context

A B2B F&B shoot is sold *with usage rights* — "the invoice includes a 1-year US social licence."
But licences (ADR 0029) and invoices were unlinked records: the operator re-keyed the grant by hand
and nothing tied the rights to the money that paid for them. The client/project recon flagged this
as the licensing-moat gap. This is the one slice of the three with a schema change + a money-page
edit, so it shipped last, on its own, for a focused review.

## Decision

Link a licence to the invoice it was granted with, and let the operator spawn one from the invoice
page — **without touching the money path**.

- **Schema (migration 078).** `licenses.invoice_id` (nullable) + an index. Every existing licence
  (and any granted outside an invoice) reads NULL and is unaffected. Additive, forward-only;
  rollback is plain `DROP COLUMN` (SQLite 3.45+).
- **Grant from the invoice (`POST …/invoices/{id}/grant-license`).** Creates a **stub** licence —
  holder = the invoice's client, with `project_id` + `invoice_id` set and the operator's title —
  audited, then **redirects to the existing licence editor** for term / territory / channels. It
  reuses the whole ADR-0029 licence editor rather than duplicating that complex form on the invoice
  page. The licence starts as a draft the operator fills in and activates; nothing auto-arms rights.
- **Money path untouched.** This writes only a separate `licenses` row; it never changes the invoice
  total, the line items, the deposit, or any payment. The licence *fee* stays operator-entered on the
  licence (never shown to the client, ADR 0029); the invoice total is whatever the operator set.
- **Visible both ways.** The invoice page lists the licences granted with it (link to each editor);
  the company command view (ADR 0034) shows a "via invoice" link on a licence that carries an
  `invoice_id`.

## Consequences

- The rights moat is now wired to the money: from an invoice the operator declares the licence once
  (no re-entry), and the link is visible from the invoice, the company view, and the audit log.
- **§11.4 / money-path holds:** no auto-charge, no auto-activation; the grant is an explicit operator
  act that produces a *draft* licence, and the invoice's money fields are never touched. The
  line-item parser — the sensitive part of the money path — is deliberately left untouched.
- **Schema:** one nullable column + index; existing rows read NULL. Red-light migration → reviewed
  draft PR; verified up + rollback on a fresh DB.
- Completes the three-slice F&B-spine batch (company view → recurring forecast → AR/statements →
  this), all consistent with the per-company command view as the home surface.

## Alternatives considered

- **Per-line-item licence fields on the invoice (parse `license_scope_N` etc.).** Rejected — it
  entangles the money-path line-item parser (where an off-by-one is a wrong charge) with rights
  metadata. A separate linked record keeps the money path pristine.
- **Auto-create the licence as `active`.** Rejected — rights are sensitive; a draft the operator
  reviews and activates (the existing flow) is the §11.4-safe default.
- **Duplicate the full licence form (territory/channels/term) on the invoice page.** Rejected —
  the stub-then-edit handoff reuses the single source of truth (the ADR-0029 editor) and keeps the
  invoice page focused.
