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

Detailed deployment notes live in [docs/SAAS-DEPLOYMENT.md](docs/SAAS-DEPLOYMENT.md).

## Operator Workflow

On the root hosted domain, `/admin/login` uses `MISE_ADMIN_PASSWORD` and opens
`/admin/saas`. Tenant subdomains still use each tenant's hashed admin password.

The operator console shows:

- tenant count, active/trialing/support counts, and readiness state
- per-tenant billing status and Stripe IDs
- custom-domain pending/verified state
- isolated data path and tenant DB presence
- manual support actions for billing status and domain verification

This keeps support simple enough for one founder.

## Self-Hosted Mode

The original self-hosted product remains the default:

- `MISE_SAAS_MODE=false`
- one SQLite database at `MISE_DATA_DIR/mise.db`
- admin password from `MISE_ADMIN_PASSWORD`
- no hosted billing or tenant routing

Do not deploy SaaS conversion work to `flow:/opt/mise` or
`kleephotography.com` unless explicitly requested.

## Development

```bash
python -m pytest tests/test_saas.py tests/test_saas_preflight.py tests/test_saas_hosted_smoke.py -q
ruff check .
ruff format --check .
```

Full smoke:

```bash
MISE_DATA_DIR=$(mktemp -d) MISE_SECRET_KEY=test MISE_ADMIN_PASSWORD=pw \
  python -m pytest tests/test_smoke.py -q
```

## Launch Copy

Mise is a hosted client studio for solo photographers and videographers:
professional galleries, booking paperwork, payments, portals, and workflow
reminders for exactly $20/month.

No tiers. No setup. Hosted and maintained for you.
