# ADR 0053 — Hosted beta gate + signup welcome email

**Status:** Accepted (launch Phase 1 activation slice)
**Date:** 2026-07-02
**Deciders:** Kevin (owner), principal engineer

## Context

The launch-readiness audit found two week-one gaps in the signup funnel:

- **Signup is fully open the moment `SAAS_MODE` is deployed** — a "private beta" isn't
  private: anyone who finds the domain can provision a real tenant (SQLite DB + media dirs)
  with 14 days of free access, gated only by the 5/hour/IP throttle.
- **Signup sends nothing.** A user who abandons Stripe Checkout is bounced back to
  `/pricing` with no record their studio exists — the slug subdomain is unguessable later,
  so an abandoned checkout is a *lost account*, not just a lost conversion. The dead
  `?trial=1` success-URL parameter also meant converters saw no confirmation after checkout.

## Decision

**1. Invite gate, one env flip.** `MISE_SAAS_INVITE_CODE`: when set, `POST /start-trial`
requires the exact code (constant-time compare, checked **before** any provisioning) and the
pricing form shows an invite field; when empty, signup is open. Beta → public launch is
unsetting one variable — no code change, no redeploy semantics beyond env.

**2. Welcome email on signup.** After `create_tenant`, a deferred `BackgroundTask` (same
non-blocking pattern as the password-reset mail) emails the owner their studio URL, sign-in
link, trial length, first-step nudge (install a preset), the export/delete ownership promise,
and the support link. It rides on **both** exits — the Stripe-checkout redirect and the
no-Stripe redirect — precisely so the abandoned-checkout user still holds a durable link back
to their studio. Sends only when the mailer is configured; failures log, never block signup.

**3. Checkout confirmation.** The login page now honors the `?trial=1` success-URL parameter
with a "you're all set — your free trial is active" notice, so the first post-checkout screen
confirms the purchase state.

## Consequences

- A private beta is actually private, with a zero-effort path to public.
- Abandoned checkout no longer strands the account — the email carries the studio URL, and
  conversion recovery (a re-checkout button on the billing page) is the next slice.
- Platform transactional email (welcome, reset) legitimately sends from the operator address;
  this remains distinct from tenant→client email identity, which is its own Phase-1 slice.
- Green-light change: no money path, no auth semantics, no schema. Reviewed draft PR.

## Alternatives considered

- **Allowlist of invited emails.** Rejected for beta scale — a single shared code is enough
  for 10–15 invites and needs no storage; per-email codes can come with referral mechanics
  later if ever needed.
- **Email verification before provisioning.** Deferred — it adds a step to the funnel's most
  fragile moment; the welcome email already proves deliverability, and the invite gate bounds
  abuse during beta.
