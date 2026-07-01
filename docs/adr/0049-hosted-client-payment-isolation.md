# ADR 0049 — Hosted client-payment isolation (fail-closed, per-tenant Stripe)

**Status:** Accepted (critical money-boundary fix — hosted mode; gates the managed launch)
**Date:** 2026-07-01
**Deciders:** Kevin (owner), principal engineer

## Context

Client invoices offer an online "pay now" path (`app/public/pay.py`) that opens a Stripe
Checkout session and records the payment on a signature-verified webhook. The Checkout
session was created with `api_key=config.STRIPE_SECRET_KEY` — the **platform operator's**
Stripe key — and `features.stripe_enabled()` read that same global key.

In single-tenant mode that is correct: the operator *is* the studio. In hosted mode
(ADR 0047) it is a **money-boundary violation**: a paying studio's *client* would pay an
invoice into the **operator's** Stripe account, not the studio's. The studio never
receives the funds through Mise, and the operator becomes an unwilling intermediary for
every tenant's client payments. This is the top money-correctness blocker after identity
(ADR 0048) and it blocks charging for the managed plan.

Note this is distinct from the **platform subscription** charge (the operator charging the
tenant $20/mo via `SAAS_STRIPE_PRICE_ID`), which *legitimately* uses the operator key — that
path is unchanged.

## Decision

Make the client-invoice money path resolve the Stripe credentials of the **serving
context**, and **fail closed** in hosted mode:

- `features.client_stripe_secret_key()` / `client_stripe_webhook_secret()` return the
  operator's global key **only in single-tenant mode** (unchanged). In hosted mode they
  return the **tenant's own** stored key (`tenants.client_stripe_secret_key` /
  `client_stripe_webhook_secret`), or `""` when the tenant has not connected its own
  Stripe. `stripe_enabled()` / `stripe_webhook_enabled()` now derive from these resolvers.
- `pay_invoice` charges with `features.client_stripe_secret_key()`; the existing
  `stripe_enabled()` guard already returns a 503 when hosted-and-unconfigured, so the
  operator's platform key **can never** be used to charge a studio's client.
- The client-facing invoice already degrades gracefully when payments are off ("To pay,
  reply to the email thread — online payment is being set up"), so a hosted client whose
  studio has not connected Stripe sees a clean message, never a broken or mis-routed charge.
- `saas_preflight` adds a **`client_payment_isolation`** check that fails the launch if,
  with no tenant in context, the client-invoice charge path still resolves a non-empty key
  (i.e. the operator key would be used) — a regression tripwire, enforced not conventional.

Storage is two nullable columns on the control-DB `tenants` table, added idempotently via
`_ensure_column` (no data migration; existing rows read NULL = off = fail-closed).

## Consequences

- **The money boundary is enforced in code, not convention** — hosted client payments are
  off until a studio connects its *own* Stripe, and the operator key never touches a
  studio's client charge. This closes the top money-correctness launch blocker.
- **Single-tenant is byte-for-byte identical** — `SAAS_MODE` off ⇒ resolvers return the
  global key exactly as before; self-hosted studios keep taking card payments unchanged.
- **Red-light (money-path) change** → reviewed draft PR with tests; no self-merge.
- **Deliberately deferred to the Stripe Connect follow-up:** the tenant-facing "connect
  your Stripe" onboarding UI, and per-tenant webhook routing at the shared
  `/webhooks/stripe` endpoint. Until Connect lands, hosted client-invoice online payment
  stays fail-closed OFF (no charge is created), so there is no unrecorded-payment gap. The
  columns and resolvers added here are exactly the seam Connect will populate.

## Alternatives considered

- **Leave the operator key and reconcile/pay out manually.** Rejected — it makes the host a
  money transmitter for every tenant, a legal and trust catastrophe, and contradicts the
  "your studio, your data (and your money)" positioning of ADR 0047.
- **Ship full Stripe Connect now.** Deferred, not rejected — Connect (onboarding + charging
  on the connected account + connected-account webhooks) is the complete answer and the
  named follow-up. Fail-closed isolation is the correct, shippable *first* slice: it removes
  the money-correctness defect immediately with a small, reviewable change, and never lets a
  charge land in the wrong account in the interim.
- **A single per-tenant secret in env.** Rejected — instance-per-customer means the tenant's
  own credentials belong with the tenant record in the control DB, not in one global env
  shared across studios (the very coupling ADR 0047 rejects).
