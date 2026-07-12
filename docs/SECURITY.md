# Mise security playbook

Operational security reference for Mise (self-hosted single-tenant and hosted
multi-tenant). What to rotate, how sessions die, what the logs can prove, and what to
do on a bad day. Design rationale lives in the ADRs (0048–0055, 0061–0065).

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
| `MISE_TELEGRAM_TOKEN` | env | Revoke via BotFather, update env, restart | Alerts pause; nothing user-facing. |
| `MISE_SAAS_INVITE_CODE` | env | Change/unset, restart | Gates new signups only. |
| Sidecar bearer tokens (`MISE_ARGUS_TOKEN`, `MISE_ODYSSEUS_CAPTION_TOKEN`, `MISE_PLATEKIT_API_TOKEN`, `MISE_SHOTS_TOKEN`, `MISE_VISION_CHALLENGER_TOKEN`) | env, each shared with one peer | Provision the new token on the **inbound** peer first (its gate keeps accepting the old until you drop it), swap the outbound peer, restart both; where no overlap window exists, accept a brief coordinated-restart gap. Rotate on suspected exposure; else on the standard interval. | Only that one sidecar's calls; inbound gates fail **disarmed** (503, feature dormant) if misconfigured, never open (ADR 0069). |

Never commit any of these; `.env` is gitignored and `.env.example` carries placeholders
only. Tests must never require real secrets.

**Sidecar transport (ADR 0069).** An armed sidecar on a non-loopback host must use
`https://`; cleartext `http://` is acceptable only to a loopback host. At startup Mise logs
one `WARNING` per armed endpoint that is `http://` to a non-loopback host (naming the env var
and, for the vision challenger, its client-media exposure) — heed it and move that endpoint to
TLS. The consolidation in ADR 0068 removes these tokens and hops entirely as each capability
becomes in-process or a direct hosted-vendor (TLS) API call.

## Sessions: how access actually dies

- **Password reset/rotation evicts admin sessions** for that context (ADR 0063) — this
  is the primary "kick the intruder out" lever.
- Gallery visitor access is a **server-side random token** in the tenant DB — delete
  the row to kill it.
- Portal/workspace client sessions are tenant-bound signed claims (ADR 0062); they die
  on `SECRET_KEY` rotation or expiry (`MISE_SESSION_MAX_AGE`, default 90 days).
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
Stripe dashboard.

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
backups run and the restore drill has been done (runbook §10).
