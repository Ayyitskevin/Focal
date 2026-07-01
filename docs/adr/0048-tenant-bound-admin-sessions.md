# ADR 0048 — Tenant-bound admin sessions (hosted auth isolation)

**Status:** Accepted (critical security fix — hosted mode; gates the managed launch)
**Date:** 2026-06-29
**Deciders:** Kevin (owner), principal engineer

## Context

The admin session cookie was a single constant payload — `sign("admin")` — signed with the one
global `SECRET_KEY`, and `is_admin()` authenticated on `unsign(cookie) == "admin"` with **no tenant
or role binding**. In single-tenant mode that is fine. In hosted mode (ADR 0047) it is a
**critical cross-tenant privilege-escalation defect**:

- A logged-in tenant's own cookie, copied to another tenant's subdomain, authenticated as that
  studio's admin — full read/write of another customer's clients, contracts, financials, galleries.
- The same cookie presented at the platform/root host authenticated the **operator console**
  (`require_platform_admin` only additionally required `current_tenant()` to be `None`, which is
  true at the root host) — exposing the whole tenant roster, customer emails, MRR, and billing
  overrides.

The cookie is host-only (no `Domain=`), so browsers won't auto-send it cross-subdomain, but a
trivial devtools/curl copy defeats that. This blocks charging anyone.

## Decision

Bind the session to the **principal of the host that minted it**, and verify the cookie against the
principal of the host serving each request (`security.admin_principal` / `is_admin`).

- **Single-tenant (default):** principal is the legacy `"admin"` — **unchanged**, so existing
  self-hosted sessions keep working with no forced logout.
- **Hosted:** principal is `"tenant:<slug>"` on a tenant host and `"operator"` on the platform/root
  host. Login mints the cookie with the current context's principal; `is_admin()` accepts the
  cookie only if its payload equals the serving context's principal.

A tenant's cookie replayed at another tenant → payload `tenant:alpha` ≠ context `tenant:beta` →
rejected. Replayed at the operator console → `tenant:alpha` ≠ `operator` → rejected. The principal
cannot be forged into another value without the server's signing key.

## Consequences

- **Cross-tenant and tenant→operator escalation are closed** — the top launch blocker from the
  hosted-layer review is fixed, with regression tests that assert a foreign-tenant and a tenant
  cookie are both rejected at the operator console (the coverage that was previously absent — the
  old tests even reused one `sign("admin")` for both contexts, encoding the bug as acceptable).
- **No migration, no schema change** — pure auth-logic change behind `SAAS_MODE`; the single-tenant
  path is byte-for-byte identical (`admin_principal` returns `"admin"` when `SAAS_MODE` is off).
- **Red-light (auth) change** → reviewed draft PR; ships with tests, no self-merge.
- **Still to harden (separate PRs, tracked):** provision-after-payment + signup throttle, webhook
  idempotency ordering, dunning grace instead of instant block, password reset, and the
  money/integration isolation gate (no shared operator Stripe/email across tenants). This ADR
  fixes identity; those fix the rest of the "safe to charge" checklist.

## Alternatives considered

- **Set `Domain=` and rely on host-only cookies alone.** Rejected — does not stop a manual cookie
  copy; the payload must carry identity that is *checked*, not merely scoped by the browser.
- **Encode a random per-session token in the control DB and look it up.** Deferred — heavier
  (server-side session store) than needed to close the escalation; the signed context-principal is
  sufficient and keeps the stateless cookie model. A server-side session table is a fine later
  hardening (enables remote logout / rotation) but is not required to fix C1.
- **Separate cookie names per context.** Rejected — same signing key means the payload check is the
  real guard; multiple cookie names add surface without adding security.
