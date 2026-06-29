# ADR 0036 — AR aging buckets + per-company statements

**Status:** Accepted (F&B/commercial spine; builds on ADRs 0024, 0034)
**Date:** 2026-06-29
**Deciders:** Kevin (owner), principal engineer

## Context

Money-ops (ADR 0024) shows total outstanding AR and a flat "past due" count, but not *how stale*
the money is — a single 90-days-late invoice and ten just-overdue ones look the same. And there was
no per-company statement: to reconcile with a client you had to read the global ledger and filter by
eye. Both are read-only reporting gaps the invoicing recon flagged; this slice closes them.

## Decision

Two read-only additions, no schema change, no money mutation.

- **AR aging buckets (money-ops).** A **pure, unit-tested** `aging_buckets(rows, today)` partitions
  open-invoice balances into *not yet due* / *1–30* / *31–60* / *61–90* / *90+* by days past the due
  date (a `deposit_paid` invoice owes total − deposit; no due date → current). The money-ops pane
  renders the bands as tiles, the 61–90 and 90+ bands highlighted. Same open-AR definition as the
  existing overdue tile, so the numbers reconcile.
- **Per-company statement.** `GET /admin/studio/companies/{id}/statement?start=&end=` lists every
  issued invoice and every payment for the whole **group** (client + venues, via `_group_ids`) in an
  optional date range, with "invoiced in range" / "received in range" totals. `?format=csv`
  downloads the invoice ledger (date, invoice, status, total, paid, balance + a TOTAL row) as an
  attachment. Dates are validated to `YYYY-MM-DD` (a junk param is ignored, never interpolated).
  Entry point: a "Statement" link on the company view.

## Consequences

- The operator can see at a glance which AR is genuinely stale (chase the 90+ band first), and hand
  a client a clean per-company statement for reconciliation — the two reporting gaps closed.
- **Pure read, low blast radius:** no schema, no writes, no money-path change; the bucketer and the
  statement are aggregation over existing invoices/payments. The aging math is a pure function with
  unit tests, so the bands can't silently drift.
- **§11.4 untouched:** nothing here sends, charges, or decides; both surfaces say so.
- Completes the two read-only F&B slices; the licence↔invoice coupling (the schema + invoice-form
  change) is the remaining, separately-reviewed slice.

## Alternatives considered

- **Aging by invoice age (issued date) instead of by due date.** Rejected — "days past *due*" is the
  collections-relevant axis; an invoice on net-60 isn't late on day 31. Due date is the honest pivot.
- **A full statement PDF.** Deferred — HTML (print-to-PDF in the browser) + a CSV cover reconciliation
  today without adding a PDF dependency; a server-rendered PDF can follow if needed.
- **Aging on the financials page rather than money-ops.** Rejected — money-ops is the "what needs
  chasing" pane; aging belongs next to the past-due tile it refines.
