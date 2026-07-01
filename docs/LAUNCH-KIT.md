# Mise $20 Hosted Launch Kit

Mise is a hosted client studio for solo photographers and videographers:
professional galleries, proposals, contracts, Stripe invoices, client portals,
workflow reminders, and niche presets for exactly `$20/month` after a 14-day
trial.

Use this launch kit for the public hosted product. It is not a deploy runbook
for the existing KLP production service.

## Public Landing Page Copy

**Headline**

Professional client studio, hosted and maintained for you.

**Subhead**

PIN-gated galleries, proposals, contracts, invoices, and client portals in one
quiet workspace. No setup, no plugin stack, no tier maze. Just `$20/month` after
a 14-day trial.

**Offer**

- Exactly `$20/month`
- 14-day free trial
- No paid tiers
- No setup fee
- Cancel anytime
- F&B and wedding presets included

**Value Proof**

Mise replaces the small-stack chaos solo creatives usually stitch together from
gallery delivery tools, client CRMs, form builders, invoice links, spreadsheets,
and reminder apps. The product stays intentionally focused so a solo founder can
host, support, and improve it without turning the $20 plan into a tier maze.

## One-Command Hosted Deployment

After `.env` contains the hosted values from `.env.example`, launch the hosted
instance with:

```bash
MISE_CADDY_SITE_ADDRESS='mise.example.com, *.mise.example.com' docker compose up --build -d
```

Then run the launch gate:

```bash
python scripts/hosted-preflight.py
python scripts/smoke-saas-hosted.py
```

The preflight should return `READY` with `0 fail`. A warning for outbound email
is acceptable only before public launch; configure SMTP before inviting real
customers.

## 5-Post X Launch Thread

1. Mise is now hosted: a professional client studio for solo photographers and
   videographers. Galleries, proposals, contracts, invoices, portals, and
   workflow reminders in one calm place. Exactly `$20/month` after a 14-day
   trial.

2. The problem: solo creatives keep stitching together gallery tools, CRMs,
   forms, contracts, invoice links, spreadsheets, and reminders. It works until
   the client experience starts feeling patched together.

3. Mise keeps the promise simple: private galleries, proofing, booking
   paperwork, Stripe invoices, client portals, and project workflows. No paid
   tiers. No setup fee. No plugin stack.

4. New trials can start with F&B or wedding presets, load realistic demo data,
   and replace it with real clients. The goal is a working client loop before
   the trial ends: offer, project, paperwork, payment, delivery.

5. Hosted Mise is for solo creatives who want a professional client studio
   without paying agency-software prices. `$20/month`, cancel anytime. Start the
   14-day trial: https://mise.example.com/pricing

## Prioritized 7-Day Launch Checklist

**Day 1: Merge and stage**

- Merge the hosted launch PR after CI passes.
- Deploy a staging hosted instance with `MISE_SAAS_MODE=true`.
- Run `python scripts/hosted-preflight.py` until there are no failures.

**Day 2: Stripe rehearsal**

- Use Stripe test mode to start a trial from `/pricing`.
- Confirm Checkout creates subscription metadata with tenant id and slug.
- Send test webhooks to `/webhooks/stripe/saas`.
- Open `/admin/billing` and the Stripe billing portal for the test studio.

**Day 3: Tenant workflow rehearsal**

- Create one F&B trial studio and one wedding trial studio.
- Load demo data in `/admin/onboarding`.
- Confirm proposals, contracts, invoices, galleries, and account settings open.

**Day 4: Domain and operator support**

- Point a test subdomain at the hosted instance.
- Save a custom domain in `/admin/account`.
- Confirm `/admin/saas` shows billing state, domain state, active MRR, trial
  pipeline, support queue, and launch checklist.

**Day 5: Marketing assets**

- Capture screenshots of `/`, `/demo`, `/pricing`, `/admin/onboarding`, and
  `/admin/saas`.
- Replace `mise.example.com` in this launch kit with the real hosted domain.
- Schedule the 5-post X launch thread.

**Day 6: Soft launch**

- Invite 3-5 trusted photographers or videographers.
- Watch trial signup, first login, onboarding score, and Stripe events.
- Fix only launch blockers; defer nice-to-have product ideas.

**Day 7: Public launch**

- Publish the X thread.
- Pin the pricing link.
- Check `/admin/saas` twice that day for support queue and trial pipeline.
- Record common objections and convert them into README/demo/pricing copy.
