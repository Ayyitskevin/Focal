# ADR 0054 — Tenant self-serve Stripe connection (bring-your-own keys)

**Status:** Accepted (launch Phase 1 — turns client invoicing ON for hosted tenants)
**Date:** 2026-07-02
**Deciders:** Kevin (owner), principal engineer

## Context

ADR 0049 made hosted client payments **fail-closed**: a client invoice charges the
tenant's own Stripe key or nothing — never the platform operator's. Correct, but the
launch audit found the write path was never built: *no route or UI sets the per-tenant
columns*, so every hosted tenant had online payment permanently off while the pricing
page sells "proposals → contracts → Stripe invoice". The core money feature didn't work.

## Decision

A **"Client payments" panel on `/admin/account`** where a tenant pastes their own Stripe
**secret key** and **webhook signing secret** (bring-your-own-keys; full Stripe Connect
onboarding remains a later upgrade, as ADR 0049 planned):

- **Both fields are required.** The webhook is how Mise marks the invoice paid after the
  charge — a connection without it silently never records payment, so it is not optional.
  The panel shows the tenant's exact per-studio endpoint URL
  (`https://<slug>.<root>/webhooks/stripe`) and the two events to subscribe.
- **Keys are verified before saving.** Format first (`sk_`/`rk_`, `whsec_`), then a live
  `Account.retrieve` against Stripe (off-thread): an auth-rejected key is a hard error —
  saving it would surface as a 500 on the *client's* pay click, the worst possible place.
  Transient network failures don't block the save (format already validated).
- **Secrets never render back.** The page shows a short mask (`sk_live_…1234`) plus the
  key mode (live/test) derived from the prefix; the stored values are write-only from the
  browser's perspective.
- **Disconnect is one click and fail-closed** — clearing the columns returns the studio to
  the ADR 0049 off state (invoice falls back to "reply to pay").
- No `updated_at` stamp (that column doubles as the dunning clock, ADR 0050).

## Consequences

- **Client invoicing now works for hosted tenants, self-serve** — the last "the product
  you're paying for doesn't function" gap from the launch audit closes.
- Money stays tenant-owned end to end: charges are created with the tenant's key, the
  webhook verifies with the tenant's signing secret, and the platform never holds funds.
- **Red-light (money-path) change** → reviewed draft PR with tests; no self-merge.
- BYO-keys means tenants handle their own Stripe account setup; the panel's inline
  instructions cover the two steps. Stripe **Connect** (OAuth onboarding, no key-pasting)
  is the planned upgrade when polish matters more than shipping.

## Review hardening (adversarial pass on this slice)

The pre-ship review confirmed two money-path defects in the first draft; both are fixed in
this slice:

- **Rotation/disconnect could lose an in-flight payment record.** A checkout link stays
  payable for ~24h and Stripe retries deliveries for days. Overwriting or clearing the
  single webhook secret mid-flight made an already-paid session unverifiable forever —
  client charged, invoice never marked paid, no log, no recovery path. Fix: the outgoing
  webhook secret is retained as `client_stripe_webhook_secret_prev` whenever it changes
  (including disconnect), and `/webhooks/stripe` verifies against **current then previous**
  secret (`features.client_stripe_webhook_secrets()`); a delivery that matches neither now
  logs a warning instead of failing silently. Disconnect still turns *new* charges off
  immediately (fail-closed pay button) while letting the trailing payment record.
- **Stripe 403 was treated as transient.** `stripe.PermissionError` (deterministic —
  a restricted key without the needed scopes) fell into the generic except and the key
  was saved anyway, with "Pay button is now live" shown: either the verify silently
  never ran, or the saved key 500'd on the client's pay click — the exact failure the
  verify exists to prevent. Fix: `PermissionError` is now a hard reject with a scopes
  message; the panel documents the minimum restricted-key scopes (Account read +
  Checkout Sessions write); the transient-branch log now includes the exception detail.

## Alternatives considered

- **Stripe Connect now.** Rejected for launch — OAuth onboarding, platform registration,
  and account-status webhooks are a week+ of work; BYO-keys delivers the same isolation
  today with two pasted strings.
- **Optional webhook secret.** Rejected — it produces the silent-failure mode where the
  client pays and the invoice never updates; requiring it prevents the support ticket.
- **Encrypting the stored keys at rest.** Deferred — the control DB already holds
  password hashes and lives on the operator-controlled volume; disk-level encryption is
  an ops concern (runbook), and a KMS dependency is out of proportion for a solo host.
  Revisit alongside Stripe Connect (which eliminates stored tenant secrets entirely).
