# Mise

Professional client studio, hosted and maintained for solo photographers and
videographers. Exactly **$20/month** after a 14-day trial. No paid tiers, no
setup fee, cancel anytime.

Mise combines the parts solo creatives usually stitch together from Pixieset,
HoneyBook, Dubsado, ShootProof, and spreadsheets:

- PIN-gated galleries, favorites, comments, proofing, and downloads
- proposals, contracts, Stripe invoices, and receipts
- client portals, package intake pages, lead forms, and booking paperwork
- Studio OS automation for reminders, tasks, packages, and project timelines
- F&B, wedding, and portrait starter presets so the first login is not blank

The hosted promise is simple:

> Professional client studio, hosted and maintained for you, only $20/month.

## Why $20 Is A Bargain

| What solo creatives need | Usually bought as | Included in Mise |
| --- | --- | --- |
| Private galleries and proofing | gallery delivery platform | yes |
| Proposals, contracts, invoices | client CRM | yes |
| Package inquiry pages | website plugin/form stack | yes |
| Workflow reminders | automation tool or spreadsheet | yes |
| Hosted maintenance | developer/server time | yes |
| Niche starting point | paid templates/course | yes |

One customer can run the full inquiry -> booking -> paperwork -> payment ->
delivery loop without graduating into a more expensive tier.

## Quickstart (Self-Hosted)

From a fresh clone to a running studio in three commands:

```bash
pip install -r requirements.txt
cp .env.example .env   # set MISE_SECRET_KEY and MISE_ADMIN_PASSWORD
python -m uvicorn app.main:app --port 8400
```

Open `http://localhost:8400/admin/login` (a LAN IP or server address works
too) and sign in with your `MISE_ADMIN_PASSWORD`. Then your first gallery:

1. **Studio → Automation** — install a niche preset (F&B, wedding, or
   portrait) so packages, lead forms, and workflow reminders aren't blank.
2. **Galleries → New gallery** — name it, upload photos (thumbnails and web
   sizes derive automatically).
3. **Publish with a 4-digit PIN** and send the client their `/g/<slug>` link
   and PIN. That's the whole delivery loop.

For production, run the Docker stack (`docker compose up --build`) — it adds
Caddy TLS ingress and daily backups; set `MISE_BASE_URL` to your public URL so
emailed links and copy-link buttons carry the right address. Full deploy notes:
[docs/SAAS-DEPLOYMENT.md](docs/SAAS-DEPLOYMENT.md) and the operator runbook.

## Hosted SaaS Mode

Hosted mode is dormant by default. Self-hosted installs keep the original
single-database behavior unless `MISE_SAAS_MODE=true`.

Hosted mode adds:

- tenant resolution by subdomain or verified custom domain
- isolated SQLite database and media tree per studio
- 14-day trial and Stripe subscription billing for the flat $20 plan
- root-host operator console at `/admin/saas`
- public demo tour at `/demo`
- launch readiness checks with `python scripts/hosted-preflight.py`
- onboarding checklist, demo data, and niche preset packs
- signup source tracking from tagged trial links such as
  `/pricing?utm_source=newsletter&utm_campaign=beta`
- operator growth analytics for activation, active rate, launch score, trial
  risk, and acquisition sources
- manual trial nudge drafts for setup help, trial rescue, conversion, and
  billing recovery follow-up
- tenant CSV export from `/admin/saas/export.csv`
- optional tenant-admin announcement banner with `MISE_SAAS_ANNOUNCEMENT`

Production billing uses:

- `MISE_STRIPE_SECRET_KEY`
- `MISE_SAAS_STRIPE_PRICE_ID` for the exactly $20/month recurring Stripe Price
- `MISE_SAAS_STRIPE_WEBHOOK_SECRET` for `/webhooks/stripe/saas`

The price is locked in code at `2000` cents. Do not make it configurable unless
the product model changes.

## One-Command Local SaaS Launch

```bash
cp .env.example .env
# edit .env with hosted values
docker compose up --build
```

Minimum hosted env:

```bash
MISE_SECRET_KEY=change-me
MISE_ADMIN_PASSWORD=change-me
MISE_BASE_URL=https://mise.example.com
MISE_COOKIE_SECURE=true
MISE_SAAS_MODE=true
MISE_SAAS_ROOT_DOMAIN=mise.example.com
MISE_SAAS_MARKETING_HOST=mise.example.com
MISE_SAAS_CONTROL_DB_PATH=/data/saas-control.db
MISE_SAAS_TENANT_DATA_DIR=/data/tenants
MISE_SAAS_TRIAL_DAYS=14
MISE_STRIPE_SECRET_KEY=sk_live_xxx
MISE_SAAS_STRIPE_PRICE_ID=price_xxx
MISE_SAAS_STRIPE_WEBHOOK_SECRET=whsec_xxx
```

Run readiness checks before launch:

```bash
python scripts/hosted-preflight.py
python scripts/smoke-saas-hosted.py
```

For a trial user the first ten minutes look like: open `/pricing`, start the
14-day trial (invite code required while the beta gate is on), get the welcome
email with your studio's own subdomain, sign in, and follow the onboarding
checklist — install a preset, publish a lead path, add a project, publish a
delivery surface. The checklist is derived from real studio state, so it
completes itself as you work.

Detailed deployment notes live in [docs/SAAS-DEPLOYMENT.md](docs/SAAS-DEPLOYMENT.md).
Launch copy, the 5-post X thread, and the 7-day launch checklist live in
[docs/LAUNCH-KIT.md](docs/LAUNCH-KIT.md).
The beta invite email and security checklist live in
[docs/BETA-LAUNCH.md](docs/BETA-LAUNCH.md).

## Operator Workflow

On the root hosted domain, `/admin/login` uses `MISE_ADMIN_PASSWORD` and opens
`/admin/saas`. Tenant subdomains still use each tenant's hashed admin password.

The operator console shows:

- tenant count, active/trialing/support counts, and readiness state
- acquisition source breakdown from `utm_source`, `utm_campaign`, and referrer
- launch score and at-risk trial counts for retention follow-up
- activation rate, active rate, average launch score, and top source
- trial nudge mail drafts for high-leverage retention follow-up
- CSV export for beta cohorts, revenue, launch health, and acquisition source
- per-tenant billing status and Stripe IDs
- custom-domain pending/verified state
- isolated data path and tenant DB presence
- manual support actions for billing status and domain verification

This keeps support simple enough for one founder.

Tenant admins can also see a lightweight hosted announcement banner when
`MISE_SAAS_ANNOUNCEMENT` is set. Use it for launch notes, new presets, or beta
office hours. It only appears inside tenant admin, never as a public gallery or
package-page footer.

## Self-Hosted Mode

The original self-hosted product remains the default:

- `MISE_SAAS_MODE=false`
- one SQLite database at `MISE_DATA_DIR/mise.db`
- admin password from `MISE_ADMIN_PASSWORD`
- no hosted billing or tenant routing

Do not deploy SaaS conversion work to `flow:/opt/mise` or
`kleephotography.com` unless explicitly requested.

## Development

The CI gate, locally:

```bash
python -m pytest -m unit -q     # fast hermetic suite (includes the hosted product)
ruff check .
ruff format --check .
```

Full smoke (needs ffmpeg for the video-pipeline tests):

```bash
MISE_DATA_DIR=$(mktemp -d) MISE_SECRET_KEY=test MISE_ADMIN_PASSWORD=pw \
  python -m pytest tests/test_smoke.py -q
```

CI also runs `pip-audit` against the pinned dependency tree; a finding there
means "bump the pin" (see `docs/SECURITY.md`).

## Launch Copy

Mise is a hosted client studio for solo photographers and videographers:
professional galleries, booking paperwork, payments, portals, and workflow
reminders for exactly $20/month.

No tiers. No setup. Hosted and maintained for you.

Public launch assets:

- landing-page offer and value proof in this README
- one-command hosted deployment shape in `docs/SAAS-DEPLOYMENT.md`
- production launch helper in `scripts/launch-hosted-production.sh`
- 5-post X launch thread in `docs/LAUNCH-KIT.md`
- prioritized 7-day launch checklist in `docs/LAUNCH-KIT.md`
- beta invitation email in `docs/BETA-LAUNCH.md`

Beta acquisition links can be tagged without any external analytics service:

```text
https://mise.example.com/pricing?utm_source=newsletter&utm_campaign=beta
https://mise.example.com/pricing?utm_source=x&utm_campaign=launch-thread
```

Those values are stored on the tenant record and summarized in `/admin/saas`.
