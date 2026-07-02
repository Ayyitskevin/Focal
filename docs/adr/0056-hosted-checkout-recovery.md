# ADR 0056 — Hosted checkout recovery (start/restart the subscription in-product)

**Status:** Accepted (launch Phase 1, final slice — closes the audit's biggest funnel leak)
**Date:** 2026-07-02
**Deciders:** Kevin (owner), principal engineer

## Context

Signup provisions the tenant and then redirects to Stripe Checkout — but the checkout is
abandonable, and nothing in the product could ever create another one. The Stripe portal
button only renders once `stripe_customer_id` exists (i.e. after a completed checkout).
So every abandoned-checkout trial hit the day-14 paywall on `/admin/billing` — the exact
page the middleware locks an expired tenant onto — **with no pay button**: a conversion
dead-end recoverable only by operator intervention. Likewise, a canceled subscriber who
changed their mind had no way back.

## Decision

**`POST /admin/billing/checkout`** creates a fresh $20/month subscription Checkout
session for tenants that can legitimately use one:

- Available when the tenant has **no subscription** (abandoned signup checkout) or a
  **terminally canceled** one (`canceled` / `incomplete_expired`). A live or attached
  subscription is refused with a friendly redirect — those are managed in the Stripe
  portal, and two live subscriptions must never exist.
- **Unused trial days carry over; spent trials pay immediately.** `trial_period_days`
  is the remaining days on the tenant's own trial clock (0 → omitted entirely, Stripe
  bills at once) — recovery is not a fresh 14-day grant.
- Signup and recovery share one `create_subscription_checkout` helper, so the session
  parameters (price, metadata, subscription metadata) can never drift between the two.
- Success returns to `/admin/billing?subscribed=1` (the tenant is already logged in),
  where the existing exactly-once webhook (ADR 0050) confirms the status.
- Reachable while locked out — `_billing_allowed_path` already admits `/admin/billing*`,
  which is precisely the point: the paywall page can now take money.
- The billing page renders a **Start/Restart subscription** button under the same
  conditions, with the trial-carry-over rule stated inline.

## Consequences

- **The day-14 paywall converts instead of churning** — an abandoned checkout is now a
  one-click recovery, not a support ticket.
- Canceled studios can rejoin without operator help.
- **Red-light (money-path) change** → reviewed draft PR with tests; no self-merge. No
  schema change; one route, one helper extraction, one template block.
- Trial-clock note: Mise's own `trial_ends_at` remains the access authority (ADR 0050
  documents the known small drift vs Stripe's subscription clock); recovery uses the
  Mise clock, which is the one the tenant experiences.

## Alternatives considered

- **Auto-create a session and email the link at day 14.** Rejected — auto-sending
  payment links crosses the no-auto-send doctrine; the button keeps the human in the
  loop while removing the dead end. A trial-ending *reminder* email (manual or
  automated) remains a separate, compatible follow-up.
- **Reactivate the canceled Stripe subscription in place.** Rejected — Stripe does not
  allow reactivating a fully canceled subscription; a new Checkout session is the
  supported path and reuses the existing webhook machinery unchanged.
