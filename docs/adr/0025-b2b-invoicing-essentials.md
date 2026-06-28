# ADR 0025 — B2B invoicing essentials: PO number, net terms, company billing details

**Status:** Accepted (first slice of the F&B/commercial spine direction)
**Date:** 2026-06-28
**Deciders:** Kevin (owner), principal engineer

## Context

The operator's real business is **commercial / food-and-beverage**, invoicing **companies** —
restaurants, brands, agencies — not consumers. A company's accounts-payable team needs three
things on an invoice that Mise could not record:

1. **A purchase-order reference.** AP systems match a payment to the PO the vendor was given;
   without it on the document, the invoice stalls in their queue. Mise had nowhere to put it.
2. **Net payment terms.** B2B runs on net-15/30/45/60, where the due date is *issue date +
   N days*. Mise had only a free-text `terms` note and a manually-typed `due_date`, so "net 30"
   was prose the system never acted on.
3. **Formal billing details.** A company's registered billing address, an accounts-payable
   contact distinct from the day-to-day one, and (when required) a tax/registration number —
   none had a home on the person-shaped `clients` row.

This is the first slice of the deliberate pivot to **deepen the commercial spine** (retainers,
B2B invoicing, licensing) rather than add breadth. It is additive and money-path-adjacent, so it
ships as a reviewed draft PR (red-light: migrations + invoice path).

## Decision

Make the three first-class, all operator-entered and optional, all shown only when present:

- **`invoices.po_number`** (migration 073) — the client's PO ref, printed on the admin and
  client-facing invoice.
- **`invoices.net_days`** (migration 073, default 0) — the payment window. On **send**, when
  `net_days > 0`, the due date is stamped `today + net_days` (computed by the pure, unit-tested
  `due_date_from_net_days`). When `net_days = 0` the operator's manual `due_date` is left exactly
  as before — no behavior change for the existing flow. Capped at 365 days to neutralize a typo.
- **`clients.billing_email` / `billing_address` / `tax_id`** (migration 074) — the AP contact,
  the billing address block (one free-form field, not six columns — a solo operator pastes what
  the client gives them), and the tax id. The billing address + tax id render on the invoice; the
  billing email **pre-fills the invoice send-email recipient** (falling back to the working
  contact), so the formal invoice goes to AP without re-typing.

Invoice **duplication** carries `net_days` (terms recur for a client) but **not** `po_number`
(each order has its own PO).

## Consequences

- **Positive:** an invoice to a company now carries everything its AP needs (PO, net-terms due
  date, billing block, tax id) and reaches the right inbox — the single most concrete cash-flow
  gap the codebase had for this operator.
- **Boundary preserved:** nothing here sends or charges. Recording a PO or net terms changes no
  status; the invoice stays a draft until the operator marks it sent (§11.4). The net-terms due
  date is stamped only at the existing, deliberate send step.
- **Schema:** migrations 073/074 add additive, nullable/defaulted columns; existing rows read
  NULL/0 and every invoice renders exactly as before until the operator fills them in. Both have
  matching rollbacks (DROP COLUMN; SQLite 3.45+).
- **Honest scope:** this does not add multi-contact records, per-line usage-type tagging, tax
  *computation*, or volume/overage pricing. Those remain later spine slices.

## Alternatives considered

- **Keep net terms as free text in `terms`.** Rejected — the due date is the thing AP acts on; a
  prose note never sets it, so "net 30" stayed decorative.
- **Six structured address columns (street/city/state/zip/country/+).** Rejected as premature for
  a solo operator — a single free-form billing-address block matches how the address actually
  arrives and renders cleanly; structuring it can come if a real need (e.g. tax automation) lands.
- **A separate `contacts` table for billing vs primary.** Deferred — a single `billing_email`
  covers the AP-contact need today without a join; a full contacts model is a larger, separate
  change.
- **Auto-compute the due date continuously from `net_days`.** Rejected — the due date should be
  fixed relative to the *issue (send) date*, not drift each day a draft sits; stamping it once at
  send is the correct, predictable behavior.
