# Mise Hosted Launch Playbook (Phases 3–5)

Everything code-side is done and merged; what remains needs **real accounts and a real
box**. This playbook is written to be executed either by the operator directly or by an
AI agent — each stage has a **ready-to-paste agent brief** (the `docs/sibling-briefs/`
pattern). Stages are ordered; don't skip the rehearsal.

The in-code twin of this playbook is `tests/test_launch_rehearsal.py` — the entire
customer lifecycle asserted in-process on every CI run. The manual pass below only has
to prove the *wiring* (DNS, TLS, real Stripe, real SMTP), not the state machine.

**References:** deploy steps `docs/SAAS-DEPLOYMENT.md` · ops `docs/MISE-SOLO-STUDIO-OS-RUNBOOK.md`
(§10 backups/restore) · decisions ADR 0047–0060 · env surface `.env.example`.

---

## Stage 3.1 — Accounts & infrastructure (human-only, ~1 hour)

Only the operator can do these (they need your identity/payment details):

- [ ] **Domain** registered (e.g. `getmise.com` — short, spellable on a phone call).
- [ ] **Cloudflare** free account; domain added; nameservers moved.
- [ ] **VPS**: 2 vCPU / 4 GB / 80 GB SSD is plenty for beta (Hetzner CX32-class ≈ €8/mo
      or DigitalOcean/Vultr equivalent). Ubuntu LTS, SSH key auth only.
- [ ] **Stripe** account activated (business details, payout bank).
- [ ] **Off-site backup target**: Backblaze B2 bucket (or another rclone backend),
      least-privilege credentials, and a separate rclone `crypt` password/salt escrow.
- [ ] **Gmail app password** for the platform sender (or the operator's existing one).
- [ ] Optional but recommended: **Telegram bot** (BotFather) + your chat id for alerts.

## Stage 3.2 — Server + DNS/TLS + deploy (agent-executable over SSH)

Checklist (details in `docs/SAAS-DEPLOYMENT.md`):

- [ ] Server hardening basics: non-root user, ufw allowing 22/80/443, unattended-upgrades,
      Docker + compose plugin installed.
- [ ] Cloudflare: proxied records `@`, `*`, `www` → server IP; SSL/TLS **Full (strict)**;
      **Always Use HTTPS** on.
- [ ] Origin cert: SSL/TLS → Origin Server → Create Certificate (apex + `*.domain`,
      15 years) → save as `certs/cloudflare-origin.pem` / `.key` on the server.
- [ ] `git clone` the repo; `cp Caddyfile.cloudflare Caddyfile`; `cp .env.example .env`
      and fill every uncommented hosted var (SECRET_KEY via the documented one-liner;
      `MISE_SAAS_MODE=true`; root domain; support email; **`MISE_SAAS_INVITE_CODE`** for
      the private beta; Telegram; backup remote, encryption acknowledgement, and
      `MISE_RCLONE_CONFIG_PATH`).
- [ ] Create a rclone config whose named `crypt` remote wraps the object-store remote.
      Store it outside the repo (for example `/opt/mise/secrets/rclone.conf`), set host
      UID/GID `10001:10001` and mode `0400`, escrow the crypt password/salt separately
      off-host, and set `MISE_RCLONE_CONFIG_PATH` to that absolute path. Never put the
      config contents or crypt key in `.env`.
- [ ] Run
      `MISE_CADDY_SITE_ADDRESS='domain, *.domain' ./scripts/launch-hosted-production.sh`
      — it must build the current app/backup images, pass the containerized static gate,
      stop old ingress/backup, health-check the private app, commit one encrypted backup,
      pass the runtime gate, and only then start the backup loop and public ingress.
- [ ] Verify: `https://domain/healthz`, `/pricing`, `/terms` all 200; a made-up
      subdomain shows the "unknown studio" page **with valid TLS**.
- [ ] Confirm the launch script's forced backup: the success marker names a remote
      `manifest.json` generation with `complete=true`, `failures=[]`, matching
      expected/captured counts, and every listed control/live/parked payload. Complete
      the same-generation control + live + parked quarantine drill in runbook §10.
- [ ] External uptime monitor (UptimeRobot free) on `https://domain/healthz`.

**Agent brief (paste to the agent that has SSH access):**

> You are deploying Mise hosted (repo `ayyitskevin/mise`) to a fresh Ubuntu VPS at
> `<IP>` with SSH access. Follow `docs/SAAS-DEPLOYMENT.md` exactly: harden the box
> (non-root sudo user, ufw 22/80/443, unattended-upgrades, Docker + compose), clone the
> repo to `/opt/mise`, `cp Caddyfile.cloudflare Caddyfile`, place the Cloudflare Origin
> cert pair the operator gives you at `certs/`, fill `.env` from `.env.example` (ask the
> operator for each secret — never invent or reuse values; require MISE_SAAS_INVITE_CODE
> to be set). Create a least-privilege rclone `crypt` config at
> `/opt/mise/secrets/rclone.conf`, owned by host UID/GID 10001 with mode 0400; set
> `MISE_RCLONE_CONFIG_PATH` to it and escrow its crypt password/salt separately off-host.
> Then run
> `MISE_CADDY_SITE_ADDRESS='<domain>, *.<domain>' ./scripts/launch-hosted-production.sh`.
> Do not bypass any static, health, forced-backup, or runtime gate. Afterwards run the
> Stage 3.2 verification checklist from `docs/LAUNCH-PLAYBOOK.md` and report each item
> pass/fail, including the launch script's forced manifest-committed backup and runbook
> §10's same-generation control + live + parked quarantine drill. Do not start a second
> pass while the backup lock is held.
> Never print secret values into logs or chat.

## Stage 3.3 — Stripe wiring (agent-assisted; operator clicks in the dashboard)

- [ ] **Test mode first.** Product "Mise Hosted" + recurring monthly USD **$20.00**
      Price → `MISE_SAAS_STRIPE_PRICE_ID`.
- [ ] Webhook endpoint `https://domain/webhooks/stripe/saas` with events
      `checkout.session.completed`, `customer.subscription.updated`,
      `customer.subscription.deleted` → signing secret → `MISE_SAAS_STRIPE_WEBHOOK_SECRET`.
- [ ] Put test keys into `.env` (`MISE_STRIPE_SECRET_KEY=sk_test_…`) and rerun the gated
      launch script; require runtime preflight `READY` before testing.
- [ ] Customer emails/receipts ON in Stripe settings; Billing Portal enabled
      (cancel allowed); retry schedule = Smart Retries (pairs with the 10-day grace).

**The money rehearsal (test mode, on the real box — the manual twin of
`tests/test_launch_rehearsal.py`):**

1. Signup at `/pricing` with the invite code → Stripe Checkout (card `4242 4242 4242 4242`)
   → land on `slug.domain/admin/login?trial=1` with the trial notice; welcome email arrives.
2. Operator console `/admin/saas`: the studio shows **card** on file; complete onboarding
   (preset + demo seed) → launch score 100.
3. Connect a **test-mode** tenant Stripe key + webhook secret under Account → Client
   payments; create a client invoice; pay it with the test card from the client-facing
   page; confirm the invoice flips to paid via the tenant webhook.
4. In the Stripe dashboard, simulate a failed renewal (test clock or
   `4000 0000 0000 0341` card) → studio shows the **warn** banner, stays accessible.
5. Cancel the subscription in the portal → paywall appears → **Restart subscription**
   from `/admin/billing` works.
6. Export the studio zip; then **Delete studio** → the exact Stripe subscription is
   canceled or appears in the operator reconciliation queue, `.trash` parking and the
   retired-path guard are present, the old subdomain 404s, and re-signup on that
   permanently retired slug is rejected. A different unused slug still provisions.
7. A second signup WITHOUT the invite code must bounce with 403.
8. Watch Telegram: the ops heartbeat should stay quiet; stop the backup container for a
   day only if you want to see the stale alarm fire (optional).

- [ ] **Only after all eight pass:** swap live keys (`sk_live_…`, live Price, live
      webhook + secret), rerun the gated launch script, and run steps 1→2 once more with a
      real card, then refund yourself in the dashboard.

**Agent brief:**

> Using the deployed Mise box at `<domain>` (SSH available) and the operator sharing
> their screen for Stripe dashboard clicks: walk the Stage 3.3 money rehearsal from
> `docs/LAUNCH-PLAYBOOK.md` step by step in Stripe TEST mode. You drive the app-side
> verification (curl/logs/`/admin/saas`) and tell the operator exactly what to click in
> Stripe for each step. Record a pass/fail table for the 8 rehearsal steps with evidence
> (HTTP codes, log lines, webhook delivery ids). STOP and report if any step fails —
> do not attempt live keys. Live-key cutover happens only after the operator reviews
> your rehearsal report and explicitly approves it.

## Stage 4 — Private beta (2–4 weeks, 10–15 invites)

- [ ] Legal: counsel skim of `/terms` + `/privacy` (ADR 0052 flags this).
- [ ] Set `MISE_PLAUSIBLE_DOMAIN` → funnel analytics go live on the marketing pages
- [ ] In the Plausible dashboard, add the three custom-event goals the pages fire:
      `Trial Submit`, `Invite Gate Rejected`, `Waitlist Joined` — the funnel becomes
      measurable in Plausible, not just in the control DB
      (marketing pages ONLY — studios are never tracked, ADR 0060).
- [ ] Send invites (template in the transformation launch kit): personal note + invite
      code + `/pricing` link. Batch of 5 first, then 10 more after a week of quiet.
- [ ] Weekly review in `/admin/saas`: signups → **card on file** → launch score →
      active. The "trials without a card" tile is the leak to chase — those people hit
      the paywall at day 14; the automated 3-day reminder (ADR 0060) plus a personal
      mailto nudge from the console are the levers.
- [ ] Support loop: every support email becomes either a fix, a runbook line, or a
      `/support` FAQ entry. Track time-per-ticket — the solo-supportability budget.
- [ ] Exit criteria to go public: ≥10 trials, ≥5 activated **with real client data**
      (not just seed clicks — check galleries have uploads), ≥3 paid conversions, zero
      isolation/billing incidents, manifest-validated restore drill re-run once mid-beta.

**Agent brief (weekly beta review):**

> Query the Mise operator console data (`/admin/saas` + its CSV export) on `<domain>`
> and produce the weekly beta report: new signups by source; trials with vs without a
> card on file; launch scores; conversions; churn/deletes; any Telegram alerts this
> week; support themes. Compare with last week's report at `<path>`. End with the three
> highest-leverage actions for the coming week, each mapped to a concrete lever
> (reminder email, onboarding change, personal nudge, product fix). Do not send any
> email yourself — draft, and let the operator send.

## Stage 5 — Public launch

- [ ] Unset `MISE_SAAS_INVITE_CODE`, restart — signup is public (one env flip, ADR 0053).
- [ ] Announce: communities the beta users came from, the founder's own client base,
      photography newsletters. The landing copy in the launch kit is ready.
- [ ] Post-launch backlog (in priority order, from the audit): per-tenant storage
      quotas · reviewed remote retention/purge tooling · demo-page product screenshots ·
      Stripe Connect
      onboarding (replaces BYO keys) · Cloudflare for SaaS custom domains · per-tenant
      sending domains (Postmark/SES) · activation timestamps + cohort views.

---

*Maintained alongside the ADRs; update stage checklists as they complete so any agent
picking this up cold knows exactly where the launch stands.*
