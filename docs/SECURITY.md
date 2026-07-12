# Mise security playbook

Operational security reference for Mise (self-hosted single-tenant and hosted
multi-tenant). What to rotate, how sessions die, what the logs can prove, and what to
do on a bad day. Design rationale lives in the ADRs (0048–0055, 0061–0068).

## Reporting a vulnerability

Email the operator (see `/support` on the hosted marketing site, or the repo owner for
self-host). Include reproduction steps; don't test against studios you don't own.
There is no bug bounty; good-faith reports are welcomed and credited.

## Security model in one paragraph

Instance-per-tenant isolation: each studio gets its own SQLite DB and media root under
`SAAS_TENANT_DATA_DIR/<slug>/`, resolved per-request from the subdomain — cross-tenant
reads are structurally hard, not policy-filtered. All signed cookies bind the tenant id
into the payload (ADRs 0048/0062) and admin sessions bind a credential fingerprint
(ADR 0063), so cookies can't replay across studios and die on password reset. Money
webhooks are signature-verified, replay-guarded by a UNIQUE event id, and amount-
reconciled before an invoice is marked paid (ADRs 0054/0055/0064). Client payments run
on the tenant's own Stripe key, fail-closed (ADR 0053).

## Secret inventory & rotation

| Secret | Where | Rotation procedure | Blast radius of rotating |
|---|---|---|---|
| `MISE_SECRET_KEY` | env | Set new value, restart | **Every signed cookie/token everywhere dies** — all admins, clients, portals re-authenticate; outstanding reset links die. Rotate on suspected key compromise, not routinely. |
| `MISE_ADMIN_PASSWORD` (single-tenant / operator) | env | Set new value, restart | All operator/self-host admin sessions evicted instantly (ADR 0063). |
| Tenant admin password | control DB (hashed) | Owner resets via emailed link, or operator via console | That tenant's admin sessions evicted instantly (ADR 0063). Reset links are single-use, 2-hour (ADR 0051). |
| Platform Stripe secret + webhook secret | env | Rotate in Stripe dashboard, update env, restart | Subscription billing only; tenant client payments unaffected. |
| Per-tenant Stripe secret + webhook secret | control DB | Tenant re-pastes in `/admin/account` | Previous webhook secret stays verifiable for in-flight sessions (ADR 0054 rotation grace) — rotate freely. |
| `MISE_ODYSSEUS_CAPTION_TOKEN` | env | Revoke at the processor, leave it unset while investigating, update env, restart | Web/native caption drafting stops; canonical captions are unaffected. The endpoint must be direct HTTPS and non-redirecting. Keep both routes dormant until the processor privacy/idempotency release checklist passes. |
| `MISE_TELEGRAM_TOKEN` | env | Revoke via BotFather, update env, restart | Alerts pause; nothing user-facing. |
| `MISE_SAAS_INVITE_CODE` | env | Change/unset, restart | Gates new signups only. |
| Rclone backend credentials + crypt password/salt | Backup-only file named by `MISE_RCLONE_CONFIG_PATH`; crypt key separately escrowed off-host | Create a new least-privilege crypt config/key, host UID/GID 10001 and mode 0400; force and restore-check a clean generation before human-approved retirement of the old remote/key | Off-site backup/recovery only. Losing the config and escrow together makes encrypted backups unrecoverable; exposing them reveals every tenant/control backup. The file must never be mounted into `mise` or stored in `.env`. |

Never commit any of these; `.env` is gitignored and `.env.example` carries placeholders
only. Tests must never require real secrets.

## Sessions: how access actually dies

- **Password reset/rotation evicts admin sessions** for that context (ADR 0063) — this
  is the primary "kick the intruder out" lever.
- Gallery visitor access is a **server-side random token** in the tenant DB — delete
  the row to kill it.
- Portal/workspace client sessions are tenant-bound signed claims (ADR 0062); they die
  on `SECRET_KEY` rotation or expiry (`MISE_SESSION_MAX_AGE`, default 90 days).
- Native owner sessions are server-side and tenant/database-bound. Hosted offboarding sets
  a durable tenant-local admission barrier before revoking them. New login/refresh/content
  mutations fail while it is closed; workers must match the original immutable database
  identity and open admission in the same final-write transaction.
- Hosted deletion permanently records the original address in `retired_tenant_slugs`;
  control-DB triggers reject future tenant assignment to it, and routine trash/backup
  retention never deletes the reservation.
- Lockout: 5 failed PIN/login attempts per IP per context → 15 min lock
  (`pin_attempts`, tenant-scoped) + a Telegram alert at the threshold.

## Audit trail: what the logs can prove

Process logs (journald/docker logs) record — with client IP and, in hosted mode, a
`[tenant:<slug>]` label — failed PIN/login attempts, lockout threshold crossings,
admin logins, password resets, invoice views, payment recordings (event ids), amount
mismatches (ADR 0064), and webhook signature failures. Values that never appear in
logs, enforced by regression tests: passwords, PINs, session tokens, reset tokens,
Stripe keys/secrets. Contract signatures store signer name + IP in the tenant DB.
Money truth lives in the `payments` table (Stripe event ids), reconcilable against the
Stripe dashboard. Native caption instruction/context/candidate text, raw provider
errors/models, and authorization/`Idempotency-Key` header values must never appear in
process logs, analytics, or crash breadcrumbs (ADR 0068).

Native Content's safe process evidence is aggregate: closed failure reason, quota kind,
expired/scrubbed count, and busy-checkpoint warning. Investigate `unknown_outcome`, failure
or quota bursts, cleanup lag, and content-job queue growth with time windows/request IDs;
do not add suggestion/session/tenant identifiers or provider payload fields as log/metric
labels. Read-only aggregate SQL is documented in the Content operations runbook.
An accepted opaque suggestion UUID can appear in its response URL and normal access-log
path; it is identifier metadata, not caption content or a bearer credential. Restrict its
retention/redaction under access-log policy and never use it as a metric label. Request
header/provider idempotency tokens remain prohibited from logs.

## Incident response

**Suspected tenant account compromise** — reset the tenant password (evicts all their
admin sessions), review that tenant's log lines by `[tenant:<slug>]`, check `payments`
vs Stripe for anything odd, have the tenant rotate their Stripe keys in `/admin/account`.

**Suspected platform compromise** — rotate `MISE_SECRET_KEY` (kills every session
everywhere), rotate `MISE_ADMIN_PASSWORD` and platform Stripe keys, force tenant
resets as needed, then restore-from-backup is the integrity backstop (runbook §10).

**Underpaid-invoice alert (ADR 0064)** — the payment is recorded but the invoice was
deliberately left unpaid; compare the session in Stripe against the invoice, then
either collect the difference or mark paid by hand.

**Native caption processor incident (ADR 0068)** — set
`MISE_MOBILE_CONTENT_SUGGESTIONS=false`, rotate the processor token when compromise
is possible, and unset the shared endpoint/token if the existing web button must also
stop. Revoke affected owner API sessions and let the tenant cleanup sweep scrub transient
operations. Preserve request IDs, aggregate status, and cost evidence only where the
provider/processor actually reports it; never preserve caption bodies,
provider payloads, model/error strings, or idempotency keys. A busy WAL-checkpoint warning
means logical scrub committed but physical truncation needs retry. Follow
`docs/IOS-CONTENT-SUGGESTIONS-OPERATIONS.md` before re-enabling.

The provider transport is HTTPS-only, forbids URL credentials/fragments, and rejects
redirects before bearer forwarding. A web claim left after provider dispatch is never
automatically retried merely because it is old. Human provider/billing reconciliation and an
exact database-identity/caption-identity/claim-bound clear are required; the operations
runbook contains the SQL. Endpoint certificate/no-redirect behavior is a staged release
gate, especially for deployments currently using HTTP tailnet URLs. Any future retry remains
blocked until provider-side durable deduplication is proven.

Odysseus currently reports neither token usage nor cost to Mise. The tenant daily quota is
request-volume control, not a global spend ceiling. Production requires processor/provider
account budget monitoring, alerts, and a hard cutoff independent of `ai_runs`.

## Caption retention and backup boundary

`PRAGMA secure_delete=ON` supports physical overwriting in the live SQLite database, and
TTL/logout cleanup attempts `wal_checkpoint(TRUNCATE)`. Neither is an instantaneous
forensic-erasure guarantee when readers pin WAL, filesystem snapshots exist, or historical
backup objects remain. Hosted offboarding is stricter: it blocks admission, revokes/scrubs,
then requires `VACUUM` and WAL truncation before parking.

Offboarding is moved off the async loop, but compaction can hold SQLite locks, take time,
and need another database-sized working copy plus temp/filesystem headroom. Capacity and
failure alerting are deployment requirements; failure aborts deletion rather than treating
partial parking as success. Admission reopens only when the control-plane deletion
reservation never committed; otherwise the tenant remains offboarded for retry/recovery.

Hosted backup builds a whole `.generation-<stamp>` on the data filesystem. It snapshots the
control DB first, uses that exact copy as the live/parked inventory, sanitizes and verifies
every destination DB, writes expected/captured identities plus `failures` into
`manifest.json`, and atomically publishes the directory. Restored native API sessions/tokens
are revoked; push devices/tokens are disabled/cleared; pending native deliveries/content
jobs are failed; active usage is finished; and in-flight suggestions are content-free
`failed/session_ended` and never resume.

Off-site protection requires a least-privilege rclone `crypt` config at the absolute
`MISE_RCLONE_CONFIG_PATH`, readable only by host UID/GID 10001 at mode 0400 and mounted
read-only into `backup`. The crypt password/salt is separately escrowed and recovery-tested
off-host. The media leg permits only exact control-derived `media`, `brand`, and `receipts`
roots; it denies live DB/WAL/SHM/journal files, scratch/export ZIPs, and orphan roots. The
generation payload copies next with the manifest excluded; `manifest.json` copies last as
the remote commit record.

A restore must use one stamp with `complete=true`, `failures=[]`, matching expected/captured
counts, and the same-generation control/live/parked archive set. Download DBs and media into
quarantine, never directly into live paths. DB snapshots are point-in-time, but the media
mirror runs afterward and is not atomic with them; `tenants-history/<stamp>` contains
displaced objects, not a full snapshot, so operators reconcile current/history against the
DB. Remote history, old committed generations, and legacy raw objects are never
automatically purged. Any deletion requires inventory plus explicit human approval.

Hosted and single-tenant backup passes hold non-blocking exclusive file locks; timer and
manual invocations cannot concurrently snapshot, prune, or sync. Lock contention is a loud
failed/skipped pass, not evidence of freshness. Sanitized backups invalidate native
credentials/push state, not every signed browser/portal cookie: rotate `MISE_SECRET_KEY` and
affected passwords when compromise is part of the restore reason.

## Dependencies & headers

- `requirements.txt` is fully pinned; CI's `dependency-audit` job fails the build on
  any published CVE (`pip-audit`). A finding means "bump the pin", not "code broke".
- Global response headers: CSP (object/frame-ancestors/form-action/base-uri locked;
  `unsafe-inline` is a documented HTMX/Alpine tradeoff), `nosniff`, `X-Frame-Options:
  DENY`, `Referrer-Policy: same-origin`, Permissions-Policy, HSTS when cookies are
  secure. Locked by tests in `tests/test_security_slice5.py`.

## Deployment assumptions (the model holds only if these do)

TLS terminates at Cloudflare/Caddy per `docs/SAAS-DEPLOYMENT.md`; `MISE_COOKIE_SECURE`
is on in production; `MISE_TRUSTED_PROXY_CIDRS` names your own ingress only (ADR 0058);
the app runs as a single worker (the in-process rate limiter assumes it, ADR 0057);
native provider work uses its dedicated bounded pool; backups run, historical raw copies
have a documented human-owned lifecycle, crypt-key escrow has been exercised, and both
same-generation active + parked restore drills have been done
(runbook §10 and ADRs 0051/0057/0068).
