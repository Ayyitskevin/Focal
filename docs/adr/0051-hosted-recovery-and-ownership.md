# ADR 0051 — Hosted recovery & ownership (password reset, studio export, studio delete)

**Status:** Accepted; amended 2026-07-11 for offboarding identity and recovery
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

**3. Studio delete — close admission, retire slug, cancel billing, trash-park.**
`POST /admin/delete-studio` requires the typed slug **and** the current password. It sets the
tenant database's durable `offboarding` barrier, revokes mobile API sessions, scrubs
transient caption-provider input/output, and requires `VACUUM` plus a successful WAL truncate
before retention. In one control transaction it inserts the original slug into
`retired_tenant_slugs`, keeps that slug on the now-deleted/canceled tenant row, records the
internal storage key `.tenant-<tenant-id>-<timestamp>`, and durably queues every exact Stripe
subscription id observed across the scrub window. It then moves the data directory to
`SAAS_TENANT_DATA_DIR/.trash/<storage-key>`, installs a read-only marker/symlink guard at the
retired live path, and stamps `storage_parked_at`. Routing ignores deleted rows; the slug is
never recycled or converted into the storage key.

Each queued subscription gets at most one automatic cancellation attempt. A successful
response or authoritative terminal webhook resolves that exact row; an ambiguous failure is
never blindly retried and stays visible for operator reconciliation. The session cookie is
cleared and the owner lands on the platform pricing page.

The ordering is an identity boundary, not cosmetic. A crash after control commit leaves the
original slug retired and the offboarding sweep completes storage parking/guard installation.
A failure before the durable control-plane deletion reservation reopens mobile admission,
while a failure after it stays offboarded and retryable. Workers bind final writes to the
original database path plus its immutable database identity, so neither a replaced file nor
a changed recovery path can receive the deleted studio's in-flight result.

The route offloads secure scrubbing/parking from the async loop, but `VACUUM` and checkpoint
can take time, hold locks, and require roughly another database-sized working copy plus
temp/filesystem headroom. Insufficient capacity or a busy checkpoint aborts deletion rather
than parking a partially scrubbed database.

## Consequences

- **The sales page and the product now say the same thing** — reset/export/delete are
  self-serve, which is the difference between claiming data ownership and demonstrating it.
- **Support load drops** for the single most common account issue (forgotten password).
- **Abandonment cannot silently keep charging.** Exact subscription ids enter a durable
  outbox in the deletion transaction. Confirmed cancellation resolves them; ambiguity stays
  alerted and human-owned rather than being retried or treated as success.
- **Deleted slugs are permanently retired.** Database triggers reject tenant inserts/updates
  to a retired slug, and routine trash/backup retention never removes that reservation.
  Late mobile/provider work must also match the immutable DB identity and open admission
  marker in its final transaction.
- **Control schema is additive.** `deleted_at` plus the retired-slug table/triggers are
  control-plane state. Single-tenant mode
  is untouched: all routes 404 without a tenant in context (self-host resets the env-var
  password, as before).
- **Red-light change (auth surface + a Stripe call)** → reviewed draft PR with tests.

## Review hardening (adversarial pass on this slice)

An adversarial review of the diff surfaced defects that this slice fixes before shipping —
each is a direct consequence of *combining* self-serve delete (new here) with pre-existing
behavior:

- **Slug-only session principal was not an identity.** ADR 0048 bound the admin cookie to
  `tenant:<slug>`. The current design permanently retires deleted slugs and also carries the
  immutable tenant **id** (`tenant:<id>:<slug>`) in the principal. The id remains necessary
  defense-in-depth for legacy installations where an address had already been reassigned
  before retirement records were introduced. (Refines ADR 0048's principal format.)
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
- **Delete ordering could strand a canceled subscription or orphan identity.** Stripe was
  canceled before durable control state, and moving data without an independent permanent
  slug record left path identity ambiguous. Fix: mobile admission closes first; retirement,
  deleted/canceled control state, internal storage identity, and exact-subscription outbox
  commit while the row keeps its original slug. The directory then parks under the internal
  key and the retired live path becomes a fail-closed guard. Delete is idempotent and the
  sweep completes an interrupted park without reusing the public slug.
- **Locked-out owners couldn't export or delete.** `_billing_allowed_path` gated an expired
  tenant to billing-only, but export/delete are exactly what a leaving customer needs. Fix:
  both are added to the allowed set.

## Alternatives considered

- **Server-side reset-token table.** Rejected — the hash-fingerprint binding gives
  single-use semantics statelessly; a table adds schema and cleanup for no extra safety.
- **Hard-delete the data dir immediately.** Rejected — an irreversible rm behind one form
  is how a mistyped confirmation becomes a catastrophe. Trash parking is the default.
  Optional local hard purge exists but is disabled at `0` days unless an operator explicitly
  sets a recovery window; it never removes the retired slug or claims deletion from remote
  current/history/generations.
- **Make a deleted slug assignable again.** Rejected — filesystem paths, old links, cookies,
  queued work, and human expectations outlive deletion. `retired_tenant_slugs` is permanent
  during routine operation/retention; the control-row tombstone preserves billing linkage
  without weakening that reservation.

## Recovery boundary added by native Content

Trash parking is recoverable but not automatically reversible. Recovery stops app/backup,
selects one manifest-committed generation with `failures=[]` and matching expected/captured
counts, and restores the same-generation control plus parked archive into quarantine. It
identifies the immutable tenant id and chooses a **new** slug/path that is neither active nor
retired; the original reservation is permanent and is never deleted, transferred, or
bypassed. Only control-derived `media`, `brand`, and `receipts` roots may leave media
quarantine, and the retained tombstone's retired-path guard must be reconstructed/verified.

The operator reconciles Stripe/outbox truth before setting an access-bearing status, verifies
schema, foreign keys, path/control/database identity, and only then clears
`mobile_runtime_state.offboarding` in `BEGIN IMMEDIATE`. Sanitized snapshots keep native API
sessions/tokens revoked, push disabled, pending native jobs failed, and transient operations
scrubbed; recovery requires a new login/push registration and cannot resume them. Media is an
eventual mirror rather than an atomic companion to the DB generation, so current/history is
reconciled manually. Any address already assigned on a legacy pre-reservation installation
is never disturbed. The exact fail-closed sequence lives in runbook §10 and
`docs/IOS-CONTENT-SUGGESTIONS-OPERATIONS.md`.
