# Mise Hosted SaaS Deployment

This runbook is for the hosted $20/month Mise product. It is separate from the
live KLP deployment on flow at `/opt/mise`.

## Product Invariants

- Hosted mode is opt-in with `MISE_SAAS_MODE=true`.
- The price is exactly `$20/month`, locked in code as `2000` cents.
- There are no other paid tiers.
- Each tenant gets an isolated SQLite database and media tree under
  `MISE_SAAS_TENANT_DATA_DIR`.
- The self-hosted KLP deployment remains default mode with `MISE_SAAS_MODE=false`.

## Required Environment

```bash
MISE_SECRET_KEY=change-me
MISE_BASE_URL=https://mise.example.com
MISE_COOKIE_SECURE=true
MISE_SAAS_MODE=true
MISE_SAAS_ROOT_DOMAIN=mise.example.com
MISE_SAAS_MARKETING_HOST=mise.example.com
MISE_SAAS_CONTROL_DB_PATH=/data/saas-control.db
MISE_SAAS_TENANT_DATA_DIR=/data/tenants
MISE_SAAS_TRIAL_DAYS=14
MISE_SAAS_ANNOUNCEMENT=
MISE_SAAS_ANNOUNCEMENT_URL=
MISE_STRIPE_SECRET_KEY=sk_live_xxx
MISE_SAAS_STRIPE_PRICE_ID=price_xxx
MISE_SAAS_STRIPE_WEBHOOK_SECRET=whsec_xxx
```

The Stripe Price behind `MISE_SAAS_STRIPE_PRICE_ID` must be one recurring monthly
USD price for exactly `$20.00`.

## One-Command Launch

```bash
cp .env.example .env
# edit .env with SaaS values above
MISE_CADDY_SITE_ADDRESS='mise.example.com, *.mise.example.com' docker compose up --build -d
```

## Wildcard TLS — Cloudflare fronting (the supported hosted setup, ADR 0059)

The stock `caddy` image cannot issue `*.your-domain` certificates (that needs a
DNS-01 challenge module). The supported setup fronts the deploy with Cloudflare,
whose edge holds the wildcard certificate:

1. Add your domain to Cloudflare (free plan) and move nameservers to it.
2. DNS records, all **Proxied** (orange cloud): `@ -> server IP`,
   `* -> server IP`, `www -> server IP`.
3. Zone settings: SSL/TLS mode **Full (strict)**; enable **Always Use HTTPS**.
4. SSL/TLS -> **Origin Server -> Create Certificate**, covering
   `your-domain` and `*.your-domain` (15-year validity). Save the pair as
   `certs/cloudflare-origin.pem` and `certs/cloudflare-origin.key` next to
   `docker-compose.yml` (the compose file mounts `./certs` into Caddy;
   `certs/` is gitignored — never commit it).
5. `cp Caddyfile.cloudflare Caddyfile`, then launch as below.

Client IPs stay correct: Cloudflare sends `CF-Connecting-IP`, which
`security.client_ip` prefers from trusted proxies (ADR 0058), so rate limits,
PIN lockout, and audit logs see the real visitor.

## DNS

Platform (managed by Cloudflare per the section above):

```text
@   -> server IP   (proxied)
*   -> server IP   (proxied)
www -> server IP   (proxied)
```

Custom tenant domains (`studio.customer.com`): with Cloudflare fronting, a plain
CNAME to the platform host will NOT get valid TLS — that requires **Cloudflare
for SaaS** (Custom Hostnames; free tier covers 100 hostnames), which is a
post-beta setup step. Until it's enabled, advise tenants to use their built-in
`slug.your-domain` address; the `/admin/account` custom-domain field marks a
domain verified on first request once TLS for it actually resolves.

## Stripe Webhooks

Create two webhook endpoints:

```text
https://mise.example.com/webhooks/stripe/saas
https://mise.example.com/webhooks/stripe
```

The SaaS webhook updates tenant subscription state. The invoice webhook records
client invoice payments and uses tenant metadata from Checkout to enter the right
tenant database.

## Smoke Checks

```bash
python scripts/hosted-preflight.py
curl -fsS https://mise.example.com/healthz
curl -fsS https://mise.example.com/pricing
```

Create a trial studio from `/pricing`, then verify:

```bash
curl -I https://studio-slug.mise.example.com/admin/login
```

Inside the admin:

- Open `/admin/onboarding` and load a demo preset.
- Open `/admin/account` and save studio settings.
- Open `/admin/billing` and verify status/trial fields.
- Create and pay a small test invoice in Stripe test mode before live billing.

## Operator Console

On the root hosted domain, `/admin/login` accepts the operator
`MISE_ADMIN_PASSWORD` and redirects to `/admin/saas`. This is a platform support
view, separate from tenant admin accounts. Use it to review tenant billing
states, custom-domain verification, launch readiness, and isolated tenant data
paths without opening a tenant database manually.

The console includes:

- manual trial nudge drafts for setup help, trial rescue, conversion prompts,
  and billing recovery
- `/admin/saas/export.csv` for beta cohorts, MRR, launch scores, and tagged
  acquisition sources

Set `MISE_SAAS_ANNOUNCEMENT` to show a short banner inside tenant admin areas.
`MISE_SAAS_ANNOUNCEMENT_URL` may be a relative path or `http(s)` URL. The banner
is not shown on public tenant gallery, demo, package, or marketing pages.

## Do Not Touch KLP

Do not deploy this runbook to `flow:/opt/mise` unless the task explicitly says to
convert the live KLP service. For local/GitHub SaaS work, keep changes on a branch
or PR and leave `kleephotography.com` untouched.
