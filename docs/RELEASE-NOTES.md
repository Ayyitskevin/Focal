# Mise v1.0-beta — Release Notes

**Cut date:** 2026-07-02. The first beta-ready build of the hosted product and
the self-hosted studio, after a ten-slice hardening and polish campaign
(security slices 1–5, ADRs 0061–0065; polish slices 1–4).

## Addendum — beta ops batch (2026-07-03)

Twelve slices landed between the cut above and launch, turning the console
into a beta cockpit:

- **Cockpit (A):** in-app Help & feedback per studio → operator triage queue
  (new/done); login pulse with silent-trial at-risk flags; waitlist capture on
  the invite gate + CSV; per-studio operator notes.
- **Launch surface (B):** og:image link cards; real product screenshots on
  `/demo`; Plausible funnel goals; marketing `robots.txt` + `sitemap.xml`
  (and the noindex header no longer overrides the marketing pages' meta).
- **Revenue engine (C):** one-shot win-back email after a lapse; platform
  dunning (decline notice + grace-ending warning, reset on recovery);
  operator trial extension (1–30 days, re-arms lifecycle mail); exit-reason
  capture on studio deletion.
- **Ops loop (D):** weekly operator digest email (the console's headline,
  first tick of each ISO week); feedback triage state; the public-launch
  flip verified live, with a gate badge in the console and a "Going Public"
  checklist in `BETA-LAUNCH.md`.

## What Mise is at this cut

One product, two shapes:

- **Hosted** — professional client studio at `$20/month` after a 14-day trial,
  one plan, invite-gated beta. Each studio is its own isolated SQLite database
  and media tree, resolved by subdomain. Trial → paywall → subscription →
  dunning grace → export/delete are all self-serve.
- **Self-hosted** — the same studio, single-tenant, free forever on your own
  box: three commands from clone to running (see README Quickstart).

The working loop a photographer gets: lead forms and package pages → client +
project CRM → proposal → contract → Stripe invoice (deposit/balance/full) →
PIN-gated gallery delivery with favorites, proofing, downloads, and a client
portal — with niche starter presets (F&B, wedding, portrait) so the first
login is never blank.

## Hardening highlights (security slices 1–5)

- Upload byte-caps with partial cleanup + content-disguise rejection; constant-
  time PIN compares; email-header injection stripping (ADR 0061).
- Client sessions tenant-bound — a portal/workspace cookie from one studio can
  never replay against another (ADR 0062).
- Admin sessions credential-bound — a password reset evicts every live session
  (ADR 0063).
- Payments: an invoice is only marked paid by money that actually covers it;
  short/tampered sessions record the charge, alert the operator, and never
  auto-settle (ADR 0064).
- `pip-audit` CI gate on the pinned dependency tree, tenant-attributable auth
  logs, and an operational `SECURITY.md` playbook (ADR 0065).

## Polish highlights (slices 1–4)

- **Real-browser fixes invisible to the test suite:** the CSRF origin guard
  locked browsers out of every form POST on any un-configured host (first-run
  Docker, LAN IP) — fixed with a standard Origin-vs-arrival-host check; a
  fresh clone's `cp .env.example .env` never actually loaded — fixed with a
  cwd fallback.
- **Hosted identity end-to-end:** every studio's admin, PIN pages, error
  pages, and receipts now wear the *studio's* name, never the operator's
  (template twin of the ADR 0055 email identity seam).
- Mobile: the admin invoice screen no longer scrolls sideways on phones; the
  full client journey measures zero horizontal overflow at 390px.
- Micro-copy: warm PIN gates ("Your photos are ready…"), helpful wrong-PIN and
  rate-limit pages (branded HTML for browsers, JSON for scripts), correct
  pluralization.
- Docs that are literally true: self-hosted quickstart (executed end-to-end),
  beta invite sequence that hands over the invite code, and a support playbook
  answering the ten questions beta users actually ask.

## Verified end-to-end at this cut

A brand-new studio, over live HTTP on a pristine hosted boot: invite-gated
signup → first login lands on the onboarding checklist → F&B preset install →
gallery upload/publish with PIN → client unlocks and browses → client +
project → invoice sent → client invoice view → full studio export (DB + media
in the ZIP) → checklist reads "launch-ready, 4/4". Gate: 307 unit tests, smoke
suite at its known-6 environment baseline (5 ffmpeg video tests + 1 full-run
lockout artifact), `ruff` clean, `pip-audit` clean.

## Known limitations (deliberate, documented)

- **Custom tenant domains** — beta studios run on subdomains; Cloudflare for
  SaaS is the planned post-beta upgrade (ADR 0059).
- **Email DMARC alignment** — studio mail sends through the platform mailbox
  with the studio's display name and reply-to; per-tenant sending domains are
  post-beta (ADR 0055).
- **CSP allows inline script/style** — HTMX/Alpine templates need it; the
  compensating directives are locked by tests, and the nonce refactor is the
  known next step (ADR 0065).
- **Single-worker deploy assumption** — the in-process rate limiter expects
  it (ADR 0057); scaling out means moving limiter state first.
- **Products/AI sidecars dormant** — Aphrodite stays off until budget cap,
  consent policy, and render backend are signed off (see CLAUDE.md).

## Upgrade notes

- Hosted/self-hosted admins re-log-in once (credential-bound sessions,
  ADR 0063); hosted clients re-enter portal/workspace PINs once (tenant-bound
  sessions, ADR 0062). Gallery visitor links are unaffected.
- No schema migrations beyond those already sequenced in `migrations/`.
