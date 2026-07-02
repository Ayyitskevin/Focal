# ADR 0051 — Hosted recovery & ownership (password reset, studio export, studio delete)

**Status:** Accepted (trust surface — hosted mode; converts ADR 0047's promises into buttons)
**Date:** 2026-07-01
**Deciders:** Kevin (owner), principal engineer

## Context

ADR 0047 sells the managed plan on *"your studio, your database — export or leave
anytime."* Before this change none of that was clickable:

- **No password reset (M1).** A forgotten password was a support ticket and a locked-out
  paying customer — fatal for a solo-operated host.
- **No self-serve export (M2).** The physical-isolation architecture makes "download your
  entire studio" trivially honest, but no route existed.
- **No self-serve delete (M2).** Leaving meant emailing the operator; worse, nothing
  stopped billing when a studio was abandoned.

## Decision

**1. Password reset — stateless, single-use, 2-hour emailed link.**
`/admin/forgot` (tenant hosts only) emails the **owner address on file** a link carrying a
purpose-scoped signed token (`security.sign_scoped("pwreset", …)`, same SECRET_KEY,
distinct namespace so session cookies and reset tokens can never impersonate each other).
The token binds `tenant_id` **plus a digest of the current password hash** — once the
password changes, every outstanding link dies. No token table, no schema. The response
never reveals whether the address matched (no enumeration); the POST sits in the tight
`signup` rate bucket (5/hour/IP), not the generous admin one; `/admin/forgot|reset` are
reachable by locked-out (`past_due`/expired) tenants via `_billing_allowed_path`. Platform
transactional email (reset links) legitimately sends from the host's address — this is
distinct from the per-tenant *client-facing* email isolation deferred with Stripe Connect.

**2. Studio export — one click, one zip, everything.**
`GET /admin/export-studio` (authenticated, tenant-bound per ADR 0048) streams a zip of a
**consistent SQLite snapshot** (via the backup API — correct under WAL, no torn copy) plus
every file under the tenant's data root. Temp file is deleted after the response.

**3. Studio delete — cancel billing, tombstone, trash-park.**
`POST /admin/delete-studio` requires the typed slug **and** the current password. It then:
best-effort **cancels the Stripe subscription** (deleting the studio must stop the $20
charge; failures are logged for operator follow-up and the row stays visible), renames the
slug to a `<slug>-deleted-<ts>` tombstone (frees the address, keeps Stripe linkage so any
final webhook lands on the tombstone rather than nowhere), stamps `deleted_at`
(control-DB `_ensure_column`, additive), and **moves** the data dir to
`SAAS_TENANT_DATA_DIR/.trash/<tombstone>` rather than deleting it — operator-recoverable
for the retention window (runbook), invisible to routing (slugs can't contain dots).
Session cookie is cleared; the owner lands on the platform pricing page.

## Consequences

- **The sales page and the product now say the same thing** — reset/export/delete are
  self-serve, which is the difference between claiming data ownership and demonstrating it.
- **Support load drops** for the single most common account issue (forgotten password).
- **Abandonment can't silently keep charging** — delete cancels the subscription in the
  same action.
- **No app migration; one additive control-DB column** (`deleted_at`). Single-tenant mode
  is untouched: all routes 404 without a tenant in context (self-host resets the env-var
  password, as before).
- **Red-light change (auth surface + a Stripe call)** → reviewed draft PR with tests.

## Review hardening (adversarial pass on this slice)

An adversarial review of the diff surfaced defects that this slice fixes before shipping —
each is a direct consequence of *combining* self-serve delete (new here) with pre-existing
behavior:

- **Reusable slug × slug-only session principal → cross-tenant admin.** ADR 0048 bound the
  admin cookie to `tenant:<slug>`. Because delete frees the slug and re-signup can reclaim
  it, a 90-day cookie for the old "alpha" would have authenticated against a *new* "alpha".
  Fix: the principal now carries the tenant **id** (`tenant:<id>:<slug>`); a reclaimed slug
  is a different id, so the stale cookie fails. (Refines ADR 0048's principal format.)
- **Tombstone host stayed live.** `tenant_middleware` looked up the slug without checking
  `deleted_at`, so a request to the tombstone host re-provisioned an empty data dir and the
  old password still logged in. Fix: the middleware treats a `deleted_at` row as unknown
  (404), same as a never-registered slug.
- **Export blocked the event loop.** The zip build (sqlite backup + deflate over all media)
  ran on the single-worker event loop, stalling every tenant. Fix: the route offloads to
  `run_in_threadpool`; the builder also skips the `tmp`/`zips` scratch dirs, writes the zip
  onto the tenant's own volume (not a small system tmpfs), and unlinks the temp file if the
  build raises.
- **Forgot-password: timing oracle + blocking SMTP.** The match branch sent mail
  synchronously (blocking the loop up to the 20s SMTP timeout) while the miss branch returned
  instantly — a latency oracle that defeated the no-enumeration claim. Fix: the send is a
  `BackgroundTask` fired after the response, so match and miss return with identical latency
  and the loop never blocks; the POST sits in the tight `signup` bucket (GET does not).
- **Delete ordering could strand a canceled subscription.** Stripe was canceled *before* the
  control-DB write, so a DB failure left a canceled sub against a live-looking row. Fix: the
  tombstone UPDATE commits first (durable, reversible), then the irreversible Stripe cancel
  fires best-effort; delete is idempotent (`deleted_at` guard + `WHERE deleted_at IS NULL`)
  and the tombstone slug carries the id so it can never collide.
- **Locked-out owners couldn't export or delete.** `_billing_allowed_path` gated an expired
  tenant to billing-only, but export/delete are exactly what a leaving customer needs. Fix:
  both are added to the allowed set.

## Alternatives considered

- **Server-side reset-token table.** Rejected — the hash-fingerprint binding gives
  single-use semantics statelessly; a table adds schema and cleanup for no extra safety.
- **Hard-delete the data dir immediately.** Rejected — an irreversible rm behind one form
  is how a mistyped confirmation becomes a catastrophe; trash-park + retention window
  keeps deletion honest *and* recoverable. A scheduled hard purge of `.trash` is runbook/
  follow-up work.
- **Keep the slug on the deleted row.** Rejected — it would strand the address forever
  and make re-signup with the same name impossible; the tombstone rename preserves
  billing linkage while freeing the name.
