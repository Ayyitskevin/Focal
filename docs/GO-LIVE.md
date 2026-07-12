# Mise Go-Live — the day-of sequence

One ordered list for launch day. Each step points at the doc that owns the
detail — this page orchestrates, it doesn't duplicate. Everything below the
"human-only" markers needs the operator's own accounts and judgment.

## 0. Prerequisites (human-only, done once)

Accounts in hand: domain + Cloudflare, VPS, Stripe (live), Backblaze/S3 for
off-site backups, Telegram bot for alerts, sending mailbox.
→ `LAUNCH-PLAYBOOK.md` Stage 3.1 has the exact list.

## 1. Configure

1. `.env` from `.env.example` with hosted production values — unique
   `MISE_SECRET_KEY` / `MISE_ADMIN_PASSWORD`, `MISE_SAAS_MODE=true`,
   `MISE_COOKIE_SECURE=true`, root domain + marketing host, Stripe live price
   + webhook secret, mail creds, `MISE_BACKUP_RCLONE_REMOTE`,
   `MISE_BACKUP_RCLONE_REMOTE_ENCRYPTED=true`, and the absolute
   `MISE_RCLONE_CONFIG_PATH`.
2. Create a least-privilege rclone `crypt` config outside the repo, make it
   host UID/GID 10001 and mode 0400, and escrow/test the crypt password/salt
   separately off-host. The config is mounted read-only into `backup`, never `mise`.
3. **Arm the beta gate:** set `MISE_SAAS_INVITE_CODE`. While set, signup
   refuses without the code; going public later is unsetting this one var.
   → security checklist in `BETA-LAUNCH.md`.
4. TLS: Cloudflare fronting per `Caddyfile.cloudflare` + Origin CA cert.
   → ADR 0059 / `SAAS-DEPLOYMENT.md`.

## 2. Launch

```bash
MISE_CADDY_SITE_ADDRESS='<root>, *.<root>' bash scripts/launch-hosted-production.sh
```

The script builds both current images and runs static preflight inside the built
image before touching the running stack. It then stops old Caddy/backup, starts
Mise privately, waits for health/migrations, forces one encrypted backup whose
`manifest.json` commits last, and runs runtime preflight. Only then does it start
the backup loop and Caddy. Do not replace it with direct `docker compose up`.

## 3. Verify (all scripted or clickable, ~15 minutes)

- [ ] `python scripts/smoke-saas-hosted.py` passes against the live host.
- [ ] `/`, `/pricing`, `/demo` render over HTTPS; padlock valid on a tenant
      subdomain too (wildcard cert).
- [ ] Signup **without** the invite code is refused; with it, a test studio
      provisions and the welcome email arrives with the studio URL.
- [ ] First login lands on the onboarding checklist; install a preset; create
      a gallery; publish with PIN; open it in a private window and unlock.
- [ ] Stripe **test-mode** money rehearsal (checkout → webhook → invoice
      paid) per `LAUNCH-PLAYBOOK.md` Stage 3.3, then flip to live keys.
- [ ] Export the test studio from `/admin/billing` — the ZIP contains the DB
      and media. Delete it; its subdomain now 404s, the original slug is
      permanently retired, and a different unused slug still provisions.
- [ ] Backup sidecar heartbeat is fresh; the off-site remote has a committed
      `manifest.json` with `complete=true`, `failures=[]`, and matching
      expected/captured live and parked counts; every listed regular
      control/live/parked payload is present. Runbook §10's control + live + parked
      quarantine drill has been completed from one generation.
- [ ] Telegram test alert arrives (fail a login 5× on a junk gallery —
      the lockout alert should name the tenant).
- [ ] CI on `main` is green, including `dependency-audit`.

## 4. Invite (human-only)

Send the first 5–10 invitations using the template in `BETA-LAUNCH.md` —
**including the invite code** — with tagged pricing links so `/admin/saas`
attributes signups. Success criteria and the feedback loop live there too.

## 5. Operate

- The console comes to you: a **weekly digest email** (first scheduler tick
  of each week, to `MISE_SAAS_SUPPORT_EMAIL`) carries signups, at-risk
  trials, fresh feedback, waitlist growth, and what lifecycle mail went out.
  Studio feedback and exit reasons land in the `/admin/saas` triage queue.
- Support answers: `SUPPORT-PLAYBOOK.md` (the 10 questions + operator
  actions, including trial extension and feedback triage).
- Incidents & rotation: `SECURITY.md`.
- Day-to-day: `MISE-SOLO-STUDIO-OS-RUNBOOK.md`; weekly beta review cadence in
  `LAUNCH-PLAYBOOK.md` Stage 4.

## Rollback / disaster recovery

Never restore “the latest directory” or sync a remote tree directly into `/data`.
Stop Mise and backup, choose one manifest-committed generation, and require
`complete=true`, `failures=[]`, matching expected/captured counts, and the exact
same-generation control, live, and parked archives. Download DBs and the media
mirror into quarantine; restore only control-derived `media`, `brand`, and
`receipts` roots, reconstruct retired-path guards, then validate SQLite schema,
foreign keys, tenant/database identities, and Stripe state before reopening.

The media mirror runs after DB capture and is not a point-in-time transaction;
use versioned `tenants-history` to reconcile changed/missing files. Restored
native sessions/tokens are revoked, push registrations are disabled, and pending
native deliveries/jobs are failed, so owners must log in and register for push
again. Incident recovery may also require rotating signed-cookie secrets. Finish
through the gated launch script and a new forced backup. Runbook §10 owns the
complete sequence; DNS and Stripe remain external sources of truth.
