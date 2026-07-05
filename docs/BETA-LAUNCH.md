# Mise Hosted Beta Launch

Use this checklist before inviting 5-10 trusted photographers or videographers
into the hosted `$20/month` beta.

## Security & Pre-Launch Checklist

- `MISE_SAAS_MODE=true` is set only on the hosted beta instance.
- `MISE_SAAS_INVITE_CODE` is set — while it is set, `/start-trial` refuses
  signups without the exact code, which is what makes the private beta private.
  Going public later is unsetting this one variable.
- `MISE_COOKIE_SECURE=true` is set because the hosted product must run behind
  HTTPS.
- `MISE_SECRET_KEY` and `MISE_ADMIN_PASSWORD` are unique production values, not
  copied from local or KLP deployments.
- `MISE_SAAS_STRIPE_PRICE_ID` points at one recurring monthly Stripe Price for
  exactly `$20.00`.
- Stripe test-mode Checkout, `/webhooks/stripe/saas`, and the billing portal
  have been rehearsed before live keys are used.
- The Stripe API version is pinned in code (`MISE_STRIPE_API_VERSION`, default the
  tested contract), so a `stripe-python` upgrade never silently changes API
  behavior. Moving to a newer version is a deliberate step: bump the var, rerun the
  test-mode rehearsal above, then deploy — never as a side effect of a dependency PR.
- Outbound email is configured with `MISE_GMAIL_USER` and
  `MISE_GMAIL_APP_PASSWORD` before inviting real customers — signup sends the
  welcome email carrying each studio's own URL, and the day-11 trial reminder
  depends on it too.
- `MISE_SAAS_SUPPORT_EMAIL` is set to the inbox you actually read — it is the
  public support contact **and** where the weekly operator digest lands
  (unset, both fall back to the Gmail sender).
- `python scripts/hosted-preflight.py` returns `READY` with `0 fail`.
- CI is green, including the `dependency-audit` job (see `docs/SECURITY.md`).
- `/admin/saas` launch checklist is clear or every remaining item has an owner.
- A test F&B studio and a test wedding studio can log in, load demo data, open
  billing, and reach account settings.
- The compose `backup` sidecar heartbeat is fresh, `MISE_BACKUP_RCLONE_REMOTE`
  points off-site, and the restore drill from the runbook (§10) has been done
  once for real.

## Beta Acquisition Links

Use simple tagged pricing links so `/admin/saas` can show which outreach creates
trial studios:

```text
https://mise.example.com/pricing?utm_source=email&utm_campaign=private-beta
https://mise.example.com/pricing?utm_source=x&utm_campaign=launch-thread
https://mise.example.com/pricing?utm_source=referral&utm_campaign=beta
```

Mise stores the sanitized source, campaign, and referrer on each tenant. The
operator dashboard summarizes source counts, activation rate, active rate,
average launch score, and at-risk trials.

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
Invite code: {{ invite_code }} (the signup form asks for it — beta is
invite-only)

If you try it, reply with the first thing that made you think "this saves me
time" and the first thing that felt unclear.

Thanks,
Kevin

When something breaks or confuses a beta user, answer from
[SUPPORT-PLAYBOOK.md](SUPPORT-PLAYBOOK.md) — it has the exact URL or fix for
the questions beta users actually ask.

## Beta Success Criteria

- 5 beta invites sent.
- 3 trial studios created.
- 2 beta users load demo data.
- 1 beta user creates a real proposal or gallery.
- Every confusion point is converted into either product copy, onboarding copy,
  or a launch-blocking issue.

## Going Public — the One-Variable Flip

The private beta is one env var. When the success criteria above are met and
the feedback queue in `/admin/saas` has been triaged, going public is:

1. **Flip:** remove `MISE_SAAS_INVITE_CODE` from `.env` and restart the app
   container. Nothing else changes — no deploy, no migration.
2. **Verify the funnel is open** (five minutes, in a private browser window):
   - `/pricing` no longer shows the invite-code field.
   - A test signup with a throwaway slug goes straight to the new studio's
     onboarding (delete it from the studio's own billing page afterwards).
   - `/admin/saas` shows the **Public — open signup live** badge in the page
     header. If it still says the gate is armed, the old env var is still set.
3. **Invite the waitlist.** Download the CSV from `/admin/saas` — everyone on
   it asked to be told. One plain email: Mise is open, here is the pricing
   link (tag it `utm_campaign=waitlist` so the console shows what it converts).
4. **Watch the first week.** The weekly digest reports signups, at-risk trials,
   and feedback; the rate limiter already covers `/start-trial` and `/waitlist`
   against open-signup abuse.

To close the gate again (abuse, capacity), set `MISE_SAAS_INVITE_CODE` back
and restart — signups return to invite-only and the pricing page grows the
waitlist form; existing studios are untouched.
