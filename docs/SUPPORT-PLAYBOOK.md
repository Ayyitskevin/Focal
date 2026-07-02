# Mise Beta Support Playbook

The questions beta photographers actually ask, with the exact answer. Written
for the operator (you) to answer from directly — every URL and behavior here
was verified against the running product. Deeper operational detail lives in
the runbook; security/incident procedure lives in `SECURITY.md`.

## The 10 questions

**1. "I forgot my studio password."**
Send them to `https://<their-slug>.<root-domain>/admin/forgot`. They get a
single-use reset link valid for 2 hours. Note for you: a reset also signs out
every existing admin session for that studio — that's deliberate (it evicts
anyone who shouldn't be there).

**2. "How does my client open their gallery?"**
The client needs two things from the studio's delivery email: the link
(`/g/<slug>`) and the 4-digit PIN. The PIN page tells them where to look
("enter the PIN from your email"). PINs are per-gallery, set at publish time.

**3. "My client says the PIN doesn't work / they're locked out."**
Five wrong tries locks that visitor's IP out for 15 minutes — the page says
so. Usually they mistyped or used an old email's PIN. Have the studio owner
confirm the current PIN on the gallery's settings page. The lockout clears
itself; there is nothing to reset server-side.

**4. "How do I take card payments from my clients?"**
Their studio → `/admin/account` → **Client payments**: they paste their own
Stripe secret key *and* webhook signing secret (both required — the webhook is
how an invoice gets marked paid). Until connected, the pay button simply
doesn't render and invoices say to reply by email — nothing breaks, it fails
closed. Money goes to *their* Stripe account, never through yours.

**5. "Where do my leads and inquiries go?"**
To the studio owner's email (the address they signed up with) and the inbox on
`/admin/home`. Emails a studio sends carry the studio's name with replies
routed to the owner.

**6. "Can I get my data out? Can I delete everything?"**
Yes, self-serve, both on `/admin/billing`: full studio export (their entire
isolated database + media) and studio delete (cancels billing in the same
action). This is the ownership promise from the welcome email — honor it fast.

**7. "How do I change my card or cancel?"**
`/admin/billing` → the Stripe billing portal handles card changes and
cancellation. A failed renewal gets a grace window while Stripe retries the
card before access pauses (they'll get dunning email from Stripe).

**8. "My trial is ending — what happens?"**
Card-less trials get one reminder email ~3 days out. At day 14 the studio
locks to `/admin/billing`, which has the start-subscription button; unused
trial days carry over, a spent trial bills immediately. Nothing is deleted at
the paywall — their data waits for them.

**9. "Can I use my own domain?"**
During the beta: your studio subdomain only. Custom domains are the first
post-beta upgrade on the list. (Don't promise a date.)

**10. "Something looks broken."**
Ask for the URL and what they clicked. On your side: check their row in
`/admin/saas` (billing state, launch score), then the app log filtered by
`[tenant:<slug>]` — every auth event and error is attributable per studio.
Crashes and lockout storms also reach you on Telegram if configured.

## Operator quick actions

- **Reset a studio's password for them**: send them `/admin/forgot` —
  self-serve and audited. There is deliberately no operator-sets-password
  path; if the owner's *email address* is the broken thing, correct it on the
  tenant record first, then have them run the reset themselves.
- **Underpaid-invoice alert fired** (payment recorded, invoice left open):
  compare the checkout session in the studio's Stripe dashboard against the
  invoice; the studio owner either collects the difference or marks paid by
  hand. The system deliberately never auto-settles a short payment.
- **Backups**: the compose `backup` sidecar snapshots every studio daily and
  syncs off-site when `MISE_BACKUP_RCLONE_REMOTE` is set; a stale heartbeat
  alerts on Telegram. Restore drill: runbook §10.
- **Slow/weird instance**: one shared process serves all studios — check disk
  first (`MIN_FREE_GB` floor blocks uploads before the disk fills), then the
  log around the timestamps they report.

## Tone for beta support

Answer fast, in plain words, and say what actually happened — beta users are
doing you a favor. If the answer is "that's a bug", say so, fix it or file it,
and tell them when it ships. Every confusion is either product copy to fix,
onboarding to fix, or a launch blocker (see Beta Success Criteria in
`BETA-LAUNCH.md`).
