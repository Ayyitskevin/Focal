# ADR 0015 — AI cost & activity report: COGS monitoring over the ledger

**Status:** Accepted (operations / COGS visibility)
**Date:** 2026-06-26
**Deciders:** Kevin (owner), principal engineer

## Context

The audit names **cloud cost runaway (COGS)** as a real risk of leaning on AI providers
(cloud vision, generation). The `ai_runs` provenance ledger already records `cost_usd` per
call, and the AI-ops dashboard (ADR 0013) shows a single rolled-up cost number — but there
was no way to see spend *over time* or *by capability*, which is what actually tells the
operator whether a provider's cost is trending the wrong way.

## Decision

Add `/admin/ai-cost`, a **read-only** spend report over the ledger.

- **Totals** for a window (7 / 30 / 90 days, default 30): total spend, run count, how many
  runs reported a cost, and non-OK count.
- **By capability**: spend, runs, and errors per capability, spend-descending.
- **By day**: spend + run volume per day, newest first.
- **CSV export** of the per-day series — the COGS evidence artifact.
- Window is validated against a fixed allow-list; all queries are bound-param over the
  indexed `created_at`. Linked from the AI-ops dashboard and the command palette.

## Consequences

- **Positive:** turns the ledger's per-call cost into the COGS monitor the audit asks for,
  at no new cost to the data model — it reads the append-only ledger and writes nothing.
  Local providers report `$0`, so the report naturally shows the cloud-vs-local cost split as
  the challenger work proceeds. Trivial to roll back (delete a route + template).
- **Bounded — by design:** the report is a *monitor*, not a *control*. It never caps,
  throttles, disables, or alerts-and-acts on a provider; cost remains informational and every
  spend decision stays human (consistent with ADR 0013's "not a control surface" stance and
  the project's money-path red line). A hard budget cap that *disables* a provider would be a
  separate, deliberately-reviewed change.
- **Accuracy caveat:** spend reflects only what providers report into `cost_usd`; calls that
  don't report a cost are counted in runs but contribute `$0`, which the view states plainly
  ("N of M runs reported a cost") so the number isn't mistaken for a complete bill.

## Alternatives considered

- **Fold it into the AI-ops dashboard.** Rejected — the dashboard is the at-a-glance
  "needs attention" pane; a spend report wants a window selector, a by-day series, and CSV
  export, which belong on a focused page. The dashboard links to it.
- **A budget cap that throttles/disables providers on overspend.** Rejected for this slice —
  it crosses from monitoring into automated control of a money-adjacent behavior, which the
  project keeps human. The report is the prerequisite (you measure before you cap).
- **A rollup/aggregate table for cost.** Rejected as premature — the live `GROUP BY` over the
  indexed `created_at` is trivial at single-studio volume; a rollup would add write-path
  complexity without a measured need.
