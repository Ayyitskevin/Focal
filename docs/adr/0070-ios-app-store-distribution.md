# ADR 0070 — iOS distribution: free multi-tenant companion app on the App Store

**Status:** Proposed
**Date:** 2026-07-17
**Deciders:** Kevin (owner)
**Resolves:** the O3 open question in `docs/HANDOFF-QUEUE.md` / `docs/IOS-UPGRADE.md` step 6
**Related:** ADR 0047 (MicroSaaS positioning), ADR 0066 (native auth boundary),
ADR 0067 (native client delivery), `docs/APP-STORE-GAMEPLAN.md` (execution plan)

## Context

O3 asked: is the native app a single-tenant personal app for the operator's own
studio, or a multi-tenant-aware companion for the hosted product? The question
blocked branding, server-URL provisioning, and the TestFlight/App Store path.

Kevin's direction (2026-07-17): ship the app **on the App Store** as part of making
Mise a revenue-generating micro-SaaS. That resolves O3 toward the hosted product.

Facts the decision rests on:

- ADR 0047 already pins the product model: **instance-per-customer, one flat
  $20/month hosted plan, self-host free**. Billing is Stripe subscription checkout
  on the web (`app/saas.py`), with trials, dunning grace, self-serve export/delete,
  and trust pages already implemented (ADRs 0050–0060).
- The native API is **already tenant-shaped**: tenant selection is host-first
  (never a body/header), `GET /api/v1/tenant` returns a public branding/bootstrap
  descriptor per studio host, and bearer sessions are tenant-bound (ADR 0066).
  The app's workspace object carries `api_base_url` per login. The architecture
  needs no rework to serve many studios — each studio is just a different host.
- This repo is a **product-incubation sandbox** (AGENTS.md): not deployed, no live
  users. App Store submission is therefore the *end* of a runway that includes
  hosting the SaaS for real (see the game plan), not a next-week action.

## Decision

1. **One app, free to download, on the public App Store.** The Mise iOS app is the
   companion for the hosted product. Any studio on hosted Mise (and any self-hosted
   studio) signs into *their* studio from the same binary.

2. **Tenant selection at sign-in, not at compile time.** The login flow accepts the
   studio's address (subdomain slug or full URL); the app resolves
   `GET /api/v1/tenant` on that host and brands itself from the response
   (`studio_name`, `brand_accent_hex`, `auth_methods`). No hardcoded personal-studio
   defaults ship in the App Store binary. Self-hosted studios enter their own URL —
   the same mechanism, no special build.

3. **Revenue stays on the web subscription. No Apple IAP in v1.** The app sells
   nothing: no purchase flow, no paywall, no price display tied to a buy action.
   Studio owners subscribe on the web (existing Stripe checkout); clients of studios
   pay studio invoices through the studio's own Stripe — money flows the app only
   ever *links out* to, consistent with the existing accept/sign/pay web-handoff
   boundary (ADR 0067). This is the standard companion-app model for business
   SaaS; the game plan carries a **verification item** to confirm current App Store
   Review Guidelines treatment (multiplatform-services rules change; do not assert
   compliance from memory at submission time).

4. **Distribution path: TestFlight first, then App Store.** TestFlight internal →
   external beta against a staging tenant host, then submission with a
   reviewer-accessible demo studio (see game-plan items — a demo tenant with seeded
   showcase data, credentials supplied in App Store review notes).

5. **App identity.** Bundle identifier, app name, and marketing assets belong to
   the Mise product brand, not the operator's personal studio. The operator's own
   studio is simply tenant #1.

## Consequences

- The app must make "which studio?" a first-class login step with graceful failure
  (unknown host, self-hosted URL, studio suspended/past-due 402 states).
- Account lifecycle obligations attach: Apple requires in-app account deletion
  affordances for apps with account sign-in. Mise's self-serve delete exists on the
  web (ADR 0051); the app must surface it (link-out at minimum) — game-plan item.
- A public binary raises the privacy bar: privacy manifest (`PrivacyInfo.xcprivacy`),
  App Store privacy nutrition labels, and a public privacy-policy URL (exists:
  hosted `/privacy`) must all be accurate for what the app actually collects.
- Push notifications, IAP, and per-studio white-label apps are all explicitly **out
  of scope for v1** — each is its own future decision.
- The `MiseServerBaseURL`-style compile-time provisioning (if present) becomes a
  developer convenience only; release builds must not depend on it.

## Alternatives considered

- **Paid app / Apple IAP subscription.** Rejected for v1: double-bills against the
  existing Stripe subscription, hands Apple a commission on an already-priced $20
  plan, adds receipt/entitlement sync the single-operator product doesn't need, and
  couples the sandbox's billing model to App Review cycles. Revisit only if
  app-led acquisition becomes the dominant funnel.
- **Single-tenant personal app (operator-only).** Rejected: no revenue path — it
  makes the App Store listing a private tool, conflicts with ADR 0047's product
  positioning, and Apple review disfavors single-customer business apps on the
  public store.
- **Per-tenant white-label binaries.** Rejected: N× review cycles, N× certificates,
  N× maintenance for a solo operator; nothing in the product requires it.
