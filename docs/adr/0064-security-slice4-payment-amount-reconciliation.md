# ADR 0064 — Security hardening Slice 4: payment amount reconciliation

**Status:** Accepted (production hardening — pre-beta security loop, slice 4 of 5)
**Date:** 2026-07-02
**Deciders:** Kevin (owner), security-focused architect

## Context

Slice 4 audited the money path and secret handling. Most of the surface was already
sound and is now confirmed:

- **Webhook signature + replay** — the client-invoice webhook (`/webhooks/stripe`)
  verifies the Stripe signature against the current secret, then the previous one for
  rotation grace (ADR 0054), and fails closed (400) if neither matches. Idempotency is
  enforced by `payments.stripe_event_id UNIQUE`: the INSERT and the invoice/project
  state advance run inside one `db.tx()`, so a duplicate event rolls the whole thing
  back to a no-op (ADR 0055). The platform-subscription webhook is independently
  idempotent via `saas_events` (ADR 0050).
- **Key scoping** — a charge is created with the *serving context's* Stripe key
  (`features.client_stripe_secret_key()`); `features.stripe_enabled()` fails closed
  (503) when hosted and the tenant has no key, so the operator's platform key can never
  charge a studio's client (ADR 0053).
- **No secret leakage** — Stripe secret keys and webhook secrets are only ever read for
  signing/verification or rendered masked in the connection panel; none are logged or
  returned in a response body. `.env` is git-ignored; `.env.example` carries only
  placeholders.

One real gap — a **defense-in-depth** hole in payment reconciliation:
`_record_paid_session` marked an invoice paid (and advanced the project funnel to
`retainer_paid`) based solely on the checkout session's metadata `kind`
(`deposit`/`balance`/`full`). It never compared Stripe's authoritative `amount_total`
against what the invoice actually owed for that kind. We create every checkout session
with the server-derived amount, so in the normal path they agree — but a bug, a
Stripe-side discount/coupon, or a tampered/replayed session with a smaller amount would
have **settled the invoice for less than it owed**, silently, and auto-advanced the
sales funnel on money that never arrived in full.

## Decision

Reconcile the paid amount against the server's own numbers before treating a session as
settlement (`_record_paid_session`, mirrored by the new `_expected_amount`, itself the
mirror of `next_payment`):

- `amount = int(session["amount_total"])`; `expected = _expected_amount(invoice, kind)`;
  `amount_ok = amount >= expected` (`>=` tolerates overpayment / rounding — an
  over-payment must never strand an invoice).
- **The payment row is always recorded**, at the real amount, inside the existing atomic
  `db.tx()` — a genuine charge is never dropped, and it stays visible on the receipt.
- On `amount_ok` — unchanged behavior: mark `deposit_paid`/`paid`, advance the project to
  `retainer_paid`, fire the payment workflows, enqueue the Notion sync.
- On **not** `amount_ok` — the invoice is **not** marked paid, the project funnel does
  **not** advance, and the payment workflows do **not** fire (auto-advancing or sending a
  client-facing "deposit received" message on an underpayment is the exact
  false-settlement this guard exists to prevent). The operator is alerted via
  `alerts.security_alert(...)` to reconcile by hand.

The alert is fire-and-forget on a daemon thread (dormant unless Telegram is configured),
so it can never block or fail the webhook. Because the payment INSERT is the first
statement in the transaction, a duplicate (retried) event raises `IntegrityError` before
the alert is reached — a retried underpayment neither double-credits nor re-alerts.

## Consequences

- **An invoice can only be marked paid by money that actually covers it.** A discounted,
  buggy, or tampered session records the payment for the audit trail but leaves the
  invoice open and pages the operator, instead of silently closing it short.
- **The sales funnel no longer auto-advances on a short payment** — `retainer_paid` is
  reached only when a deposit/full payment genuinely landed, matching the pipeline's
  documented "payment is the gate" invariant (migration 031).
- No schema change, no new secret, no client-visible change on the happy path
  (single-tenant and hosted exact-amount flows are byte-for-byte unchanged; the existing
  webhook lifecycle smoke test passes untouched). Adds one pure helper, one alert import,
  one early return.
- The response body gains `underpaid`/`duplicate` flags for observability; Stripe ignores
  the body on a 200, so this is internal-only.

## Alternatives considered
- **Reject the webhook (non-2xx) on an underpayment.** Stripe would then retry the event
  forever and the real payment would never be recorded — worse. Recording + alerting
  keeps the money visible and hands the decision to a human, consistent with §11.4
  (model/automation proposes, human approves).
- **Auto-refund the short amount.** A money-moving action the operator must own; out of
  scope for a security guard and against the "no auto-charge/refund" invariant.
- **Trust `kind` and reconcile in a nightly job.** Leaves a window where the invoice reads
  paid and the funnel has advanced on money that isn't there; inline reconciliation closes
  it at the source.
