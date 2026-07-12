# ADR 0060 — Launch playbook + beta instrumentation

**Status:** Accepted (Phases 3–4 pre-built; execution gated only on real accounts/infra)
**Date:** 2026-07-02
**Deciders:** Kevin (owner), principal engineer

## Context

All launch-blocking code is merged (ADR 0047–0059). What remains — VPS, domain,
Cloudflare, Stripe, beta — needs real accounts the operator must create. The operator's
direction: pre-build everything buildable now, and leave **executable instructions for
the AI agents** (or a future session) that will pick the work up when the accounts
exist. Separately, the audit's remaining beta-readiness gaps were still open: zero
funnel analytics on the marketing pages, abandoned-checkout trials indistinguishable
from healthy ones in the operator console, and no automated trial-ending touchpoint.

## Decision

**1. The rehearsal as code.** `tests/test_launch_rehearsal.py` walks the entire hosted
customer lifecycle in one narrative test — gated signup, welcome email, exactly-once
activation webhook, tenant Stripe connect (fail-closed → live), dunning grace, paywall
recovery billing immediately after a spent trial, export, delete-with-billing-cancel,
permanent slug-retirement rejection, and successful provisioning under a different
unused slug. It runs in the CI unit gate, so the state machine behind the launch is
re-proven on every push; the manual rehearsal then only has to prove wiring
(DNS/TLS/real Stripe/SMTP).

**2. The playbook as the agent interface.** `docs/LAUNCH-PLAYBOOK.md` scripts Phases
3–5 as ordered checklists with **ready-to-paste agent briefs** per stage (the
`docs/sibling-briefs/` pattern): server + deploy over SSH, Stripe wiring + the 8-step
manual money rehearsal (test mode first, live keys only after operator approval), the
weekly beta review, and public launch. Human-only steps (accounts, payments, dashboard
clicks, legal review) are explicitly marked as such; agent briefs forbid inventing
secrets and printing them.

**3. Beta instrumentation (code, this slice):**
- **Funnel analytics** — the existing Plausible config now renders on the four SaaS
  marketing pages via one shared partial, and **only** there: no tracking on tenant
  admin or client-facing surfaces (the privacy promise stays true).
- **Card-on-file visibility** — the operator console counts `card_on_file` and
  `no_card_trials` (tile + per-row badge). An abandoned-checkout trial previously looked
  identical to a healthy one until its day-14 paywall; this is the funnel's biggest
  leak made visible.
- **Trial-ending reminder** — `trial_reminder_sweep()` (hourly, platform-level, outside
  `tenant_runtime` so it carries platform identity) emails owners whose **card-less**
  trial ends within 3 days: once per tenant (`trial_reminder_sent_at` stamp, additive
  `_ensure_column`), stamped only after a successful send so failures retry. Platform
  transactional mail to the owner — same class as welcome/reset (ADR 0053); the
  client-facing no-auto-send doctrine is untouched. Trials with a card are left alone —
  they convert on their own.

## Consequences

- Launch execution is **resumable by any agent from a cold start**: the playbook says
  exactly what's done, what's next, who can do it, and how to verify it.
- The operator can answer "which trials will die at the paywall?" from one tile, and
  those tenants get an automated nudge toward the recovery checkout (ADR 0056) without
  the operator remembering to check.
- Marketing-funnel visibility exists from beta day one; studios remain untracked.
- Green-light change: one additive control-DB column, no money path, no auth semantics.

## Alternatives considered

- **A general drip-email system.** Rejected — one precise, one-shot lifecycle email is
  supportable by a solo founder; a campaign engine is not, and more sends erode the
  quiet-tool positioning.
- **Reminding carded trials too.** Rejected — Stripe already emails them at conversion;
  duplicate reminders read as churn-bait.
- **Analytics in the shared base template.** Rejected — `base.html` is extended by
  admin/login surfaces via `base_cream.html`; a marketing-only partial keeps the
  no-tracking-inside-studios promise enforceable at a glance.
