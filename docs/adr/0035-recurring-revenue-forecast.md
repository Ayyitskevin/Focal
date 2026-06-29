# ADR 0035 — Recurring-revenue forecast (studio-wide MRR + 12-month projection)

**Status:** Accepted (F&B/commercial spine; builds on ADR 0034)
**Date:** 2026-06-29
**Deciders:** Kevin (owner), principal engineer

## Context

ADR 0034 put a per-company MRR tile on the command view, but a recurring-revenue business also
needs the *whole book* in one place: current run-rate, where it's heading, and which plans need a
renewal decision soon. The retainer recon flagged this (no per-client retainer overview, no
utilisation/forecast dashboard). It's read-only and generalises queries the company view already
uses, so it's the safe next slice.

## Decision

A read-only `GET /admin/studio/recurring-revenue` over every active plan.

- **Headline:** current **MRR** (Σ active-plan monthly totals), **ARR** (MRR × 12, labelled a
  run-rate annualisation, not a promise), active-plan count, and renewals due in 90 days.
- **12-month projection** from a **pure, unit-tested** `forecast(plans, months)`: for each of the
  next 12 month keys it sums the plans still generating that month. A `pause_at_term` plan with a
  renewal date stops *after* its renewal month (the operator must renew to continue); evergreen and
  auto-rolling-term plans carry on. (`'YYYY-MM'` strings compare chronologically, so the rule is a
  one-line string compare — no date parsing in the hot loop.) The projection is explicitly a
  projection, and the page says so.
- **Renewals in 90 days** and **every active plan** (client, monthly, term) — each row links to the
  plan and to the company view.
- **Pure read.** No schema change, no money mutation; entry point is a link from money-ops.

## Consequences

- The operator can see recurring health at a glance — run-rate, the shape of the next year, and the
  renewal decisions coming — without opening each plan.
- **§11.4 / manual-send hold:** this only *projects*; it generates and charges nothing. The
  projection assumes continuation, which is honest for planning and clearly labelled.
- **Cheap and safe:** the projection core is a pure function with unit tests; the route is a single
  query + that function. No red-light surface.
- Generalises ADR 0034's MRR roll-up, so the two stay consistent (same definition of an active
  plan's monthly contribution).

## Alternatives considered

- **Per-plan revenue history (actuals) instead of a forward projection.** Deferred — actuals live
  in invoices/payments (the financials/company pages already show collected). The gap here is
  *forward* visibility; a backward "recognised recurring revenue" report can follow.
- **Model proration / mid-term rate changes in the projection.** Rejected for v1 — adds real
  complexity for marginal accuracy on a planning view; the flat per-plan monthly is the honest
  first cut, and the page is labelled a projection.
- **Put it on the company view (per-company only).** Rejected — the company view answers "this
  company"; the forecast answers "my whole book". Different questions, different pages; they share
  the roll-up query shape.
