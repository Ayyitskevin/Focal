# ADR 0065 — Security hardening Slice 5: logging, audit trail, final defenses

**Status:** Accepted (production hardening — pre-beta security loop, slice 5 of 5)
**Date:** 2026-07-02
**Deciders:** Kevin (owner), security-focused architect

## Context

The final slice audited logging/audit coverage, response-header defenses, and the
dependency surface. Most of it was already sound and is now confirmed and locked:

- **Headers/CSP** — the global middleware already ships CSP (with `object-src 'none'`,
  `frame-ancestors 'none'`, `form-action 'self'`, `base-uri 'self'`), `nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy`, Permissions-Policy, and HSTS behind
  `COOKIE_SECURE`. `unsafe-inline` for script/style is a documented tradeoff — the
  HTMX/Alpine templates use inline handlers pervasively, and removing it is a
  template-wide refactor, not a hardening tweak. The load-bearing directives were not
  covered by any test; they are now.
- **Log hygiene** — a full sweep of every `log.*` call found ids, slugs, counts, and
  IPs only. No password, PIN, session token, reset token, or Stripe secret is ever
  logged. Also now locked with regression tests on the two riskiest paths (failed
  admin login, failed webhook signature).
- **Auth/money audit events** — failed attempts (with IP), lockout threshold crossings
  (with Telegram alert), admin logins, resets, invoice views, payment recordings, and
  amount mismatches were all already logged.

Three real gaps:

1. **No dependency-CVE gate.** `requirements.txt` is fully pinned (good) but nothing
   ever audited the pins — a published CVE in FastAPI/Pillow/Stripe would sit unnoticed
   until manually discovered.
2. **Hosted auth-audit lines weren't tenant-attributable.** Logging is process-wide
   while gallery/portal ids restart per tenant, so "bad PIN for gallery 3 from <ip>"
   could not be tied to a studio during incident forensics — and the lockout alert had
   the same ambiguity.
3. **No security playbook.** Rotation procedures, session-eviction behavior, incident
   steps, and the deployment assumptions the security model rests on lived across ten
   ADRs but nowhere operational.

## Decision

- **CI `dependency-audit` job** — `pip-audit -r requirements.txt` as a separate job so
  an OSV/PyPI outage never blocks the test gate; a finding means "bump the pin". The
  current tree audits clean, so the gate lands green.
- **`security.tenant_log_label()`** — appends ` [tenant:<slug>]` (or ` [platform]`) to
  auth-audit lines in hosted mode: the PIN-failure warning, the lockout Telegram alert,
  and the admin-login line. Single-tenant log output is byte-for-byte unchanged.
- **`docs/SECURITY.md`** — vuln reporting, secret inventory with rotation procedure and
  blast radius per secret, how sessions die, what the logs can prove, incident-response
  steps (tenant compromise / platform compromise / underpaid-invoice alert), and the
  deployment assumptions the model depends on.
- **Regression tests** — CSP load-bearing directives + no-`unsafe-eval`; failed login
  never logs the attempted password; failed webhook never logs the signing secret;
  hosted failure lines and lockout alerts carry the tenant label; single-tenant label
  is empty.

## Consequences

- A published CVE in a pinned dependency now fails CI within a day of disclosure
  instead of waiting to be noticed.
- Hosted incident forensics can attribute every auth event to a studio from the
  process log alone.
- The operator has one page to act from during an incident instead of ten ADRs.
- No schema, no money path, no client-visible change; single-tenant logs unchanged.

## Alternatives considered
- **Dropping `unsafe-inline` via nonces.** The real XSS lockdown, but requires moving
  every inline handler to static JS — a product-wide refactor with breakage risk far
  beyond this loop's surgical mandate. Documented as the known next step; the locked
  object/base/form-action/frame-ancestors directives are the compensating controls.
- **A dedicated audit-log table.** Append-only DB audit trail is attractive, but the
  process log already carries every needed event with IP + tenant attribution, and a
  schema change is disproportionate pre-beta. Revisit if compliance needs arise.
- **Dependabot/Renovate.** Complementary (proposes bumps) but doesn't *gate*; the CI
  audit is the enforcement layer and works on self-hosted forks too.
