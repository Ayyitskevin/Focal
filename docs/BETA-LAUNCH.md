# Mise Hosted Beta Launch

Use this checklist before inviting 5-10 trusted photographers or videographers
into the hosted `$20/month` beta.

## Security & Pre-Launch Checklist

- `MISE_SAAS_MODE=true` is set only on the hosted beta instance.
- `MISE_COOKIE_SECURE=true` is set because the hosted product must run behind
  HTTPS.
- `MISE_SECRET_KEY` and `MISE_ADMIN_PASSWORD` are unique production values, not
  copied from local or KLP deployments.
- `MISE_SAAS_STRIPE_PRICE_ID` points at one recurring monthly Stripe Price for
  exactly `$20.00`.
- Stripe test-mode Checkout, `/webhooks/stripe/saas`, and the billing portal
  have been rehearsed before live keys are used.
- Outbound email is configured with `MISE_GMAIL_USER` and
  `MISE_GMAIL_APP_PASSWORD` before inviting real customers.
- `python scripts/hosted-preflight.py` returns `READY` with `0 fail`.
- `/admin/saas` launch checklist is clear or every remaining item has an owner.
- A test F&B studio and a test wedding studio can log in, load demo data, open
  billing, and reach account settings.
- Data volume backups are configured for the Docker host before public launch.

## Recommended Production Launch Command

From the repo root, after `.env` has hosted production values:

```bash
MISE_CADDY_SITE_ADDRESS='mise.example.com, *.mise.example.com' bash scripts/launch-hosted-production.sh
```

For Podman Compose hosts:

```bash
MISE_COMPOSE_CMD='podman compose' MISE_CADDY_SITE_ADDRESS='mise.example.com, *.mise.example.com' bash scripts/launch-hosted-production.sh
```

The script runs hosted preflight first, then starts the Docker/Caddy stack.

## Beta Invitation Email

Subject: Want to try my $20/month hosted client studio for photographers?

Hi {{ first_name }},

I am opening a small beta for Mise, a hosted client studio for solo
photographers and videographers.

It gives you private galleries, proofing, proposals, contracts, Stripe invoices,
client portals, workflow reminders, and F&B/wedding starter presets in one
simple workspace. The hosted plan will stay exactly `$20/month` after a 14-day
trial. No paid tiers, no setup fee, cancel anytime.

I am inviting 5-10 trusted creatives first because I want honest feedback before
the public launch. The beta ask is simple:

- create a trial studio
- load the F&B or wedding demo
- replace one demo record with a real workflow
- tell me where the product feels clear, confusing, or missing something

Trial link: {{ trial_link }}

If you try it, reply with the first thing that made you think "this saves me
time" and the first thing that felt unclear.

Thanks,
Kevin

## Beta Success Criteria

- 5 beta invites sent.
- 3 trial studios created.
- 2 beta users load demo data.
- 1 beta user creates a real proposal or gallery.
- Every confusion point is converted into either product copy, onboarding copy,
  or a launch-blocking issue.
