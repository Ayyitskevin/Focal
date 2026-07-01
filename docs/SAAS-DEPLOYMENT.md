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

Wildcard public TLS usually needs DNS-challenge support in Caddy. If the Caddy
image is not built with your DNS provider module, put Cloudflare/Tailscale in
front or provision tenant domains explicitly.

## DNS

Platform:

```text
mise.example.com -> server IP
*.mise.example.com -> server IP
```

Custom domains:

```text
studio.customer.com CNAME mise.example.com
```

After a tenant saves a custom domain in `/admin/account`, the domain is marked
verified the first time a request reaches Mise on that hostname.

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

## Do Not Touch KLP

Do not deploy this runbook to `flow:/opt/mise` unless the task explicitly says to
convert the live KLP service. For local/GitHub SaaS work, keep changes on a branch
or PR and leave `kleephotography.com` untouched.
