# ADR 0063 — Security hardening Slice 3: session credential binding

**Status:** Accepted (production hardening — pre-beta security loop, slice 3 of 5)
**Date:** 2026-07-02
**Deciders:** Kevin (owner), security-focused architect

## Context

Slice 3 audited authentication, sessions, and rate limiting. Most of the surface was
already sound and is now confirmed:

- **Rate-limit completeness / correctness** — login, PIN, reset, signup, forms, pay, and
  checkout all map to limiter buckets keyed on the corrected client IP (ADR 0058); the
  admin login is additionally protected by the `pin_attempts` lockout (5 fails / 15 min).
  Single-worker deploy assumption still holds (Dockerfile CMD, ADR 0057).
- **Lockout isolation** — `pin_attempts` is tenant-scoped, so one studio's failed logins
  can't lock out another studio or the operator; operator login at the root host records
  against `config.DB_PATH` (a real DB), so its lockout functions.
- **Admin auth boundaries** — `require_admin` / `require_platform_admin` and the
  `_platform_path` / `_billing_allowed_path` gates verified; admin routers carry the
  `require_admin` dependency.

One real gap: **a credential change did not invalidate existing admin sessions.** The
admin cookie is a signed *principal* (`admin` / `operator` / `tenant:<id>:<slug>`) that
encoded identity but nothing tied to the password. So after a password **reset** — the
victim's action to evict whoever is in their account — a stolen or previously-issued
admin session cookie kept working until it expired (up to the 90-day `SESSION_MAX_AGE`).
The reset link itself was already single-use (ADR 0051), but the *session* was not.

## Decision

Mix a short digest of the current admin credential into the session principal
(`security._pw_fp`), so the expected principal moves whenever the password changes and
every cookie minted under the old credential stops authenticating:

- single-tenant / operator: `f"…:{sha256(ADMIN_PASSWORD)[:12]}"`
- hosted tenant: `f"tenant:<id>:<slug>:{sha256(admin_password_hash)[:12]}"`

`is_admin` recomputes `admin_principal` per request (already the design), so no
server-side session store is needed — this is the stateless equivalent of Django's
`get_session_auth_hash`. A hosted tenant reset (`set_tenant_password`) or an
operator/self-host `ADMIN_PASSWORD` rotation now evicts all existing admin sessions for
that context.

## Consequences

- **A password reset actually locks intruders out** — the session dies with the
  credential, closing the "reset doesn't help against a live session" gap.
- **One-time re-login on the deploy that ships this** (the principal string changes for
  everyone) — a minor, documented, security-positive cost; pre-beta there are ~no hosted
  users. Self-hosted admins re-log-in once.
- No schema, no money path; `admin_principal` gains a fingerprint suffix, four test
  helpers updated to construct it. The fingerprint is a one-way digest — it never exposes
  the password/hash and only ever *rejects* a stale cookie.
- Session lifetime (`SESSION_MAX_AGE`, 90 days) is left as-is (convenience for a solo
  tool); credential binding is the higher-value control and makes the long lifetime safe
  against the reset scenario.

## Alternatives considered
- **Server-side session table with explicit revocation.** Cleaner remote-logout story,
  but a schema + lookup on every request; the credential fingerprint achieves the
  eviction-on-reset property statelessly. A session table remains a fine later addition
  if per-device logout is ever wanted.
- **Shortening `SESSION_MAX_AGE`.** Reduces exposure window but doesn't *evict* on reset;
  orthogonal, and worse UX. Kept the long lifetime + binding instead.
