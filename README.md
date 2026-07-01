# Mise

Mise is a lightweight client studio for solo photographers and videographers:
PIN-gated galleries, proposals, contracts, Stripe invoices, scheduling, portals,
and studio operations in one FastAPI + HTMX + SQLite app.

## Hosted MicroSaaS Mode

The hosted product is intentionally simple:

- **One flat paid plan:** exactly **$20/month**
- **Free trial:** 14 days
- **No paid tiers:** every hosted customer gets the same client-studio workflow
- **Target customer:** solo F&B, wedding, portrait, and video creatives who want
  a professional hosted client studio without maintaining software

Hosted mode is off by default. To run the SaaS version locally:

```bash
cp .env.example .env
# edit .env:
# MISE_SECRET_KEY=...
# MISE_SAAS_MODE=true
# MISE_SAAS_ROOT_DOMAIN=localhost
# MISE_BASE_URL=http://localhost
docker compose up --build
```

For production, set `MISE_CADDY_SITE_ADDRESS` to the platform host and tenant
wildcard, for example `mise.example.com, *.mise.example.com`, then point DNS at
the host. Tenant product data is isolated under `MISE_SAAS_TENANT_DATA_DIR`, with
one migrated SQLite database and media tree per studio.

Stripe subscription billing uses:

- `MISE_STRIPE_SECRET_KEY`
- `MISE_SAAS_STRIPE_PRICE_ID` for the exactly $20/month Stripe Price
- `MISE_SAAS_STRIPE_WEBHOOK_SECRET` for `/webhooks/stripe/saas`

The existing self-hosted mode remains the default and continues to use
`MISE_ADMIN_PASSWORD` plus the single `MISE_DATA_DIR/mise.db` database.

admin/      back-office routers (galleries, studio, invoices, contracts,
              proposals, licenses, presets, press, recurring, shotlist, uploads, activity)
              common.py (shared for splits)

Extractions (2026-06): dir_size/fmt_size/short_date/gallery_card/today moved to common.py; spark_series + get_or_404 + clients_for_select to db.py. Tests for common + fixed test_admin_common imports. CI: units (ignore smoke) + ruff check/format strict before smoke.

Ruff fixes post-extract (import hygiene, unused).

## Testing

- Unit tests (fast feedback): `python -m pytest tests/ --ignore=tests/test_smoke.py -q -m unit`
- Full smoke (e2e against temp DB): `MISE_DATA_DIR=$(mktemp -d) MISE_SECRET_KEY=test MISE_ADMIN_PASSWORD=pw python -m pytest tests/test_smoke.py -q`
- Extracted basics (healthz, security headers, CSP, CSRF) now in `tests/test_basic.py` for units.
- Lint + format enforced in CI (ruff check + ruff format --check) before smoke.
