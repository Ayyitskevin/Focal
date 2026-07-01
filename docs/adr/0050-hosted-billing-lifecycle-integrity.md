# ADR 0050 — Hosted billing-lifecycle integrity (exactly-once webhooks, dunning grace, signup throttle)

**Status:** Accepted (billing-integrity fixes — hosted mode; gates the managed launch)
**Date:** 2026-07-01
**Deciders:** Kevin (owner), principal engineer

## Context

Three defects in the hosted billing lifecycle, found in the transformation review and
sequenced after identity (ADR 0048) and money isolation (ADR 0049):

1. **Webhook idempotency ordering (lost billing events).** `/webhooks/stripe/saas`
   inserted the `saas_events` dedupe marker in its **own committed transaction**, then
   applied the billing side-effect (`update_tenant_billing`) in a second one. A crash or
   error between the two left the marker without the effect — and because Stripe's retry
   deduped against that marker, the event (a cancellation, a payment failure, a
   subscription activation) was **swallowed forever**. Billing state silently diverges
   from Stripe's truth.
2. **Instant hard-block on `past_due` (decline = churn).** `tenant_has_access` returned
   `False` the moment a subscription went `past_due`. Stripe retries a failed card over
   days; an instant lockout turns a transient decline into a locked-out — and likely
   churned — paying customer.
3. **Unthrottled signup (provisioning as a DoS vector).** `POST /start-trial` provisions a
   whole tenant instance (control-DB row, SQLite file, media root) and was in **no**
   rate-limit bucket: one IP could create tenants as fast as it could POST, exhausting
   disk and squatting slugs.

## Decision

**1. Exactly-once webhook processing.** The dedupe marker and the billing effect now
commit in the **same control-DB transaction** (`_process_saas_event`): the
`saas_events` INSERT and the `tenants` UPDATE share one connection; SQLite's context
manager commits both or rolls back both. Crash before commit → neither exists → Stripe's
retry reprocesses. Crash after → both exist → the retry is a duplicate no-op.
`update_tenant_billing` gains an optional `con=` parameter to join the caller's
transaction; every other caller is unchanged. The route body was extracted to
`_process_saas_event(event)` so tests exercise the exactly-once contract directly
without mocking Stripe signatures.

**2. Dunning grace for `past_due`.** `tenant_has_access` grants a grace window
(`SAAS_PAST_DUE_GRACE_DAYS`, default 10 days) measured from the status flip
(`updated_at`) before blocking. The billing banner turns **warn** during grace ("card
declined, Stripe is retrying — update billing") instead of block. Terminal states
(`unpaid`, `canceled`, `incomplete_expired`) still block immediately; a `past_due` row
with no `updated_at` also blocks (fail-closed, the pre-grace behavior). Repeated
`customer.subscription.updated` retries re-stamp `updated_at`, so grace effectively
tracks Stripe's own retry window and ends when Stripe lands a terminal status — which is
exactly the dunning semantics wanted.

**3. Signup throttle.** `/start-trial` gets its own `signup` rate-limit bucket —
**5/hour/IP** (`MISE_RL_SIGNUP`), against the existing in-memory sliding-window limiter —
instead of falling through unlimited. Provisioning cost is now bounded per IP per hour.

## Consequences

- **Billing state can no longer silently diverge from Stripe** — the retry contract is
  crash-safe, with tests that kill the effect mid-event and assert the event stays
  retryable.
- **A card decline is a banner, not a lockout** — the customer keeps working while Stripe
  retries, and the studio blocks only when the subscription lands in a terminal state or
  grace lapses.
- **Signup abuse is bounded** — a hostile IP creates at most 5 tenants/hour, not
  thousands.
- **No migration, no schema change** — `saas_events` and `tenants.updated_at` already
  existed; this fixes ordering and interpretation, plus one config knob and one
  rate-limit bucket.
- **Red-light (money/billing) change** → reviewed draft PR with tests; no self-merge.

## Alternatives considered

- **Provision-after-payment for signup.** Rejected for now — the 14-day trial funnel
  intentionally provisions at signup (checkout with `trial_period_days` follows), and
  reordering would gut the try-before-card funnel. The abuse vector is bounded by the
  throttle instead; true deferred provisioning can be revisited with the Stripe Connect
  onboarding work.
- **A job queue / outbox for webhook effects.** Deferred — correct at scale, but a
  single-file transaction achieves the same exactly-once guarantee here because the
  marker and the effect live in the same SQLite control DB. An outbox becomes worth it
  only if effects ever leave that DB.
- **Fixed-length grace from first decline (ignore re-stamps).** Rejected — Stripe's
  retry schedule is the ground truth for how long recovery is plausible; tracking
  `updated_at` follows it automatically instead of hard-coding a parallel clock.
