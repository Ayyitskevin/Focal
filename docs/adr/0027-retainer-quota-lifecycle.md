# ADR 0027 — Retainer deepening: quota units, per-period snapshot, advisory overage, renewal

**Status:** Accepted (second slice of the F&B/commercial spine direction; follows ADR 0025)
**Date:** 2026-06-28
**Deciders:** Kevin (owner), principal engineer

## Context

Recurring retainers are the operator's **#1 mode** (restaurants/brands on monthly content). The
recurring-plan engine generated draft invoices well (ADRs around Domain G), but the *retainer
relationship* was thin: the deliverable **quota was advisory free-text labels with zero billing
effect**, there was **no overage path**, and a plan ran **forever** with no term or renewal
signal. Editing a quota silently rewrote history, so any "what was owed" figure drifted.

Three competing designs (minimal / full-lifecycle / money-safety-first) were generated and
adversarially judged. The synthesis grafts the minimal additive spine with two correctness
hardenings, and the owner chose the recommended option on all three forks: **assisted overage
pre-fill** (in a follow-up money-path PR), **counts + unit** quota, and **remind + optional
pause** for renewal.

## Decision

Deepen retainers additively, money-safe (this PR writes **no** invoice line — overage is
display-only here; the assisted pre-fill is a separate, carefully-reviewed follow-up):

- **Quota units + overage rate (no migration).** `parse_quota` widens each line in the existing
  `recurring_plans.quota` JSON from `{label,target}` to `{label,target,unit,overage_rate_cents}`.
  `unit` is a small dropdown (images/reels/stories/…); `overage_rate_cents` is the per-unit
  charge for delivery beyond target (0 = advisory only). Legacy `{label,target}` rows parse
  unchanged via `_quota_line` defaults — the free-text label join (deliveries/calendar/captions)
  is untouched.
- **Per-period quota snapshot (migration 076).** `retainer_period_quota(plan_id, period,
  quota_json)` freezes the quota committed for a period, written **once** via `INSERT OR IGNORE`
  inside the existing `generate_for_plan` `db.tx()` at first draft-generate. So the advisory
  overage figure is measured against what was committed that month, not a later-edited quota.
- **Advisory overage (pure + read-only).** `_overage_lines` (pure, unit-tested) computes
  per-label over and a dollar `amount_cents` only where `over > 0 AND rate > 0`; `compute_overage`
  reads the snapshot (or live quota fallback) + the delivery log. The plan page shows an
  "Overage this period — advisory estimate, not billed" panel, folding the un-targeted bucket in
  with a near-match typo warning so a mislabeled over-delivery surfaces instead of silently
  dodging the estimate. **Nothing is billed; no invoice line is written.**
- **Renewal = date + one-shot nudge + optional pause (migration 076).** Nullable `term_start` /
  `renews_on` (NULL = evergreen, today's behavior), `nudged_renewal` (one-shot, re-armed only
  when `renews_on` actually changes), and opt-in `pause_at_term`. `app/retainer_reminders.py` is
  a faithful clone of `contract_reminders` (one internal Telegram nudge N days before `renews_on`,
  `RETAINER_RENEWAL_NUDGE_DAYS` default 14, no-op when alerts disabled), wired into the existing
  scheduler loop. A **Renew** action rolls the term forward (preserving its length). The
  `pause_at_term` guard skips the **unattended sweep** for periods strictly after the renewal
  month (the renewal month itself still bills); a deliberate manual Generate is the human
  override and is unaffected.

## Consequences

- **Positive:** a retainer now reads as a real relationship — structured quota with units and
  rates, an honest per-period overage estimate, and a term/renewal lifecycle — without weakening
  any guardrail. Default behavior for existing evergreen plans is byte-for-byte unchanged.
- **§11.4 holds:** generation stays draft-only; this slice computes a figure and shows it but
  writes no money. The assisted overage→draft pre-fill (the only money-path touch) ships as a
  separate, focused PR where a human still reviews and saves an editable draft.
- **Schema:** migration 076 is additive (4 nullable/defaulted columns + one table); existing
  rows read NULL/0. Matching rollback (plain DROP COLUMN, SQLite 3.45+). Red-light change →
  reviewed draft PR.
- **Honest scope (deferred):** no rollover/banking of unused quota, no controlled-vocab/FK quota
  model, no auto-renewal-proposal, and (this PR) no overage write — all documented as later
  slices.

## Alternatives considered

- **Structured quota_lines FK table.** Rejected for now — typo-proof and report-ready, but a
  multi-table, dual-join-key migration; the units-in-JSON + near-match warning gets most of the
  benefit at a fraction of the blast radius.
- **Auto-append overage to the monthly draft.** Rejected — it moves a money computation into the
  unattended scheduler, the weakest posture on §11.4. Overage flows only through an explicit
  operator action (the follow-up PR).
- **Auto-propose a renewal successor plan.** Rejected — a status-enum/renewal-chain ceremony
  over-scoped for one operator; a date + a nudge + an optional pause is the right altitude.
- **Snapshot at first delivery instead of first generate.** Rejected — a back-dated delivery
  could race a stale snapshot; first-generate is a single deterministic trigger.
