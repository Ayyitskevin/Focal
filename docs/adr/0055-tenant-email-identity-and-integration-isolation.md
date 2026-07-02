# ADR 0055 — Tenant email identity, lead routing, and operator-integration isolation

**Status:** Accepted (launch Phase 1 — closes the cross-tenant email/data leak, audit H2)
**Date:** 2026-07-02
**Deciders:** Kevin (owner), principal engineer

## Context

The launch audit's biggest remaining boundary leak: in hosted mode, **every email a tenant's
studio sent went out as the platform operator** — From `SITE_NAME <GMAIL_USER>` (literally
"Kevin Lee Photography"), ICS ORGANIZER branded the same, and email signatures signed the
operator's name. Worse, **a tenant's inbound leads and booking notifications were delivered
to the operator's private inbox** (`config.GMAIL_USER`), body text advertised
`kleephotography.com`, and the client-facing booking links were built from the platform
`BASE_URL` — dead or mis-routed on a tenant's subdomain. Finally, the operator's
personal Notion/Google-Calendar/SMS credentials were live inside tenant contexts: a
studio's bookings could mirror into the operator's Notion and calendar, and a studio's
inbox texts sent from the operator's phone number.

## Decision

**1. One identity seam in the mailer.** All outbound mail already flows through
`mailer.send`; it now derives identity from the serving context:
`From: "<studio_name>" <GMAIL_USER>` with `Reply-To: <owner_email>` in a tenant context
(caller-provided Reply-To still wins), and the legacy `SITE_NAME`, no implicit Reply-To,
in single-tenant mode — byte-for-byte unchanged. Mail still transits the operator's one
SMTP login (per-tenant SMTP/provider identities are the Stripe-Connect-era upgrade); the
display name + reply path are what a client sees and answers to. `mailer.sender_name()`
also brands ICS ORGANIZER CN lines, booking/reminder signatures, and inbox reply
subjects. The ICS ORGANIZER *mailto* stays the real sending mailbox (a mismatched
organizer address trips calendar clients).

**2. Studio-bound notifications go to the studio.** `mailer.studio_inbox()` — tenant
`owner_email` in tenant context, operator Gmail otherwise — now receives lead/inquiry/
package notifications and booking copies; lead bodies reference the actual serving host
via `urls.public_base_url()` instead of a hardcoded domain.

**3. Client links are tenant-host aware.** Booking manage/rebook links build from
`urls.public_base_url()` (tenant origin, custom-domain aware) instead of the platform
`BASE_URL`.

**4. Operator integrations fail closed in tenant contexts.** `features.operator_context()`
is false whenever a hosted tenant is in context; it now gates `sms_enabled`,
`notion_enabled`, `notion_bookings_enabled`, `notion_sessions_enabled`, and
`gcal.configured()` (which all Google sync/free-busy paths and the admin Connect card
check). Same doctrine as the client-Stripe gate (ADR 0049): a global credential never
serves a tenant. The per-tenant scheduler sweeps inherit all of this automatically
because they run inside `tenant_runtime`.

## Consequences

- **A studio's clients see the studio** — name on the From line, replies to the
  photographer, calendar invites branded correctly, booking links that work.
- **A studio's leads reach the studio owner**, not the operator's private inbox — the
  cross-tenant data disclosure is closed.
- **The operator's Notion/Calendar/phone number can no longer absorb tenant data**, even
  with all global env creds armed on the hosted box.
- Deliverability caveat (documented, not hidden): mail still sends from the operator's
  Gmail address, so strict DMARC alignment is to the operator domain. Acceptable at beta
  scale; per-tenant sending domains (Postmark/SES) are the planned upgrade.
- Platform mail (welcome, password reset at the root host) keeps platform identity;
  the reset mail on a tenant host brands as the studio — both correct.
- **Green-light change** — no money path, no auth, no schema; single-tenant behavior
  is unchanged and covered by an explicit regression test.

## Alternatives considered

- **Per-tenant SMTP credentials now.** Rejected for launch — a credentials store, a
  settings surface, and a deliverability support burden per tenant; the identity seam
  gets the customer-visible 90% with zero new secrets. Revisit with a transactional
  provider (per-tenant sending domains) post-beta.
- **From: the tenant's own address via operator Gmail.** Rejected — Gmail rewrites or
  DMARC-fails spoofed From addresses; display-name + Reply-To is the deliverable version
  of the same intent.
- **Leaving integrations enabled but namespacing the data.** Rejected — the operator's
  Notion/Calendar/number are personal accounts, not multi-tenant systems; fail-closed is
  the only honest gate until per-tenant connections exist.
