# ADR 0028 — Retainer overage → draft invoice: assisted pre-fill, never an auto-write

**Status:** Accepted (the money-path follow-up to ADR 0027)
**Date:** 2026-06-28
**Deciders:** Kevin (owner), principal engineer

## Context

ADR 0027 made retainer overage a deterministic, read-only **estimate** but deliberately stopped
short of touching the invoice — it deferred the one money-path step to its own focused PR. The
operator chose **assisted pre-fill** for overage (over display-only re-typing, and over
auto-append-to-draft which would put a money computation in the unattended scheduler). This ADR
records that seam.

The constraint is §11.4: the model proposes, deterministic code validates, **a human commits**.
Overage may not auto-invoice or auto-charge; it may at most place an *editable* line on a draft
the operator reviews and saves.

## Decision

Wire overage to the invoice through the existing draft-edit flow — the system writes **no**
invoice line; it pre-fills an editable one.

- **Trigger (recurring).** An "Add overage to draft invoice" button on the plan's overage panel
  (shown only when there's billable overage *and* an open draft) POSTs `…/overage-to-draft`. The
  handler recomputes overage **server-side**, finds the plan's open draft
  (`recurring_plan_id` + `status='draft'`, newest — past periods' drafts are sent/locked), writes
  an `overage_proposed` **audit row** capturing the figure at click time, and redirects to the
  draft with `overage_label` / `overage_qty` / `overage_unit_cents` query params. No open draft →
  it bounces back with "generate this period's draft first"; no billable overage → "set an
  overage rate". It never creates an invoice.
- **Seam (invoices).** `invoice_detail` (the **GET**) reads those params and, only on a draft with
  room, injects **one synthetic editable row** into the rendered line list — a display-only
  pre-fill. It persists nothing (it's a GET). The row becomes a real line **only if the operator
  saves**, at which point the unchanged `update_invoice` (POST) reads it like any typed row and
  recomputes the total. `update_invoice`, `mark_invoice_sent`, `pay.py`, and Stripe are untouched.

The correct seam is the GET render path, not the POST writer — a deliberate choice the design
review caught (the POST overwrites all line items wholesale and reads no params).

## Consequences

- **Positive:** an over-delivered retainer becomes a billable line in two clicks (propose → save)
  with no re-typing and no fat-finger risk, while every existing money gate (draft-locked edit,
  explicit Send, Stripe) stays exactly as it was.
- **§11.4 holds end to end:** nothing reachable from the scheduler computes or writes a money
  figure; the only write is the operator saving an editable draft. The proposal is audited even
  though no line is written until save, so the figure shown is recoverable.
- **No new schema, no new money-write code.** Pure additive: a GET pre-fill + a redirecting
  trigger + an audit row. Reverting is deleting the button and the param read.
- **Honest limits:** the button targets the plan's newest open draft (normally this period's);
  there is no per-period invoice key, so a stale unsent draft from a prior period would receive
  the line — the operator reviews the period in the editable row before saving, and the audit
  records it. A per-period invoice link is a possible later hardening.

## Alternatives considered

- **Auto-append overage when the monthly draft generates.** Rejected — moves a money computation
  into the unattended scheduler, the weakest §11.4 posture.
- **Display-only (operator re-types the line).** Rejected — safe but high-friction every month;
  the assisted pre-fill keeps the same human-save guarantee without the re-typing.
- **Pre-fill via the POST `update_invoice`.** Rejected as incorrect — that handler replaces all
  line items from the form and reads no query params; the GET render path is the right seam.
