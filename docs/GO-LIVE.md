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
   + webhook secret, mail creds, `MISE_BACKUP_RCLONE_REMOTE`.
2. **Arm the beta gate:** set `MISE_SAAS_INVITE_CODE`. While set, signup
   refuses without the code; going public later is unsetting this one var.
   → security checklist in `BETA-LAUNCH.md`.
3. TLS: Cloudflare fronting per `Caddyfile.cloudflare` + Origin CA cert.
   → ADR 0059 / `SAAS-DEPLOYMENT.md`.

## 2. Launch

```bash
MISE_CADDY_SITE_ADDRESS='<root>, *.<root>' bash scripts/launch-hosted-production.sh
```

The script runs `python scripts/hosted-preflight.py` first and refuses to
start until it reports `READY` with `0 fail`.

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
      and media. Delete the test studio; its subdomain now 404s.
- [ ] Backup sidecar heartbeat is fresh; the off-site remote shows today's
      snapshots; the restore drill has been done once (runbook §10).
- [ ] Telegram test alert arrives (fail a login 5× on a junk gallery —
      the lockout alert should name the tenant).
- [ ] CI on `main` is green, including `dependency-audit`.

## 4. Invite (human-only)

Send the first 5–10 invitations using the template in `BETA-LAUNCH.md` —
**including the invite code** — with tagged pricing links so `/admin/saas`
attributes signups. Success criteria and the feedback loop live there too.

## 5. Operate

- Support answers: `SUPPORT-PLAYBOOK.md` (the 10 questions + operator
  actions).
- Incidents & rotation: `SECURITY.md`.
- Day-to-day: `MISE-SOLO-STUDIO-OS-RUNBOOK.md`; weekly beta review cadence in
  `LAUNCH-PLAYBOOK.md` Stage 4.

## Rollback

The stack is one compose project on one volume: `docker compose down`,
restore the volume from the latest snapshot (runbook §10), `docker compose
up`. DNS and Stripe state live outside the box and survive a rebuild.
