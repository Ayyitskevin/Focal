# ADR 0034 — Per-company command view (the F&B/commercial CRM roll-up)

**Status:** Accepted (first slice of the F&B/commercial spine deepening)
**Date:** 2026-06-29
**Deciders:** Kevin (owner), principal engineer

## Context

The studio already holds everything a relationship-driven B2B operator needs — clients with a
parent/venue hierarchy (Domain A), projects, B2B invoices (PO/net-terms/billing block, ADR 0025),
structured licences (ADR 0029), and retainers with quota/overage (ADR 0027) — but it's scattered
across per-client and per-project pages. For a solo operator whose clients are *companies* (often a
group with several venues), there was no single place to answer "how is **this company** doing?"
A recon of the spine converged on this as the highest daily-value, lowest-risk next slice: it's
connective tissue over data that already exists, and it's the natural home the other candidate
slices (utilisation, AR aging, licence↔invoice) plug into later.

## Decision

A read-only `GET /admin/studio/companies/{client_id}` that rolls up the whole **group** — the
client plus every venue/region under it (`clients.descendant_ids`, so a flat client is a degenerate
group of one) — onto one page.

- **Headline tiles:** recurring/month (MRR = Σ active-plan totals), outstanding AR (issued −
  paid), past-due count + amount (same definition as money-ops: a `deposit_paid` invoice owes
  total − deposit), collected lifetime, and shoot cadence (last/next `projects.shoot_date`).
- **Recurring book:** every active plan in the group with this period's utilisation — on-track vs
  which quota lines are *behind*, and the **advisory** overage figure (via
  `recurring.compute_overage`; never a charge — §11.4/ADR 0028 hold).
- **Pipeline:** project counts per status + the live (non-closed) projects, soonest shoot first.
- **Past-due invoices, active licences, venues** — each row links to the surface that owns it.
- **Pure read.** It writes nothing and runs no money/licence mutation; it only aggregates
  invoices / payments / projects / recurring_plans / licenses. Entry point: a "Company view" link
  on the client page.

Implementation note: `recurring` is imported *locally* inside the handler (it imports
`studio.get_project` at module load, so a top-level import would cycle — the same pattern the
licences roll-up already uses).

## Consequences

- **The spine reads like a CRM:** one glance per company shows the recurring book, what's owed and
  overdue, the pipeline, the licences in force, and when they last/next shoot — the question a
  relationship operator actually asks.
- **Low blast radius:** read-only aggregation over existing tables; no schema change, no money or
  rights mutation, so it's safe to ship without a red-light review.
- **A foundation, not a dead end:** the recon's other slices slot straight in — AR aging buckets
  on the AR tile, a studio-wide recurring-revenue forecast generalising the MRR roll-up, and the
  licence↔invoice coupling surfacing here once it lands.
- **Hygiene:** added a `*.db` / `*.db-wal` / `*.db-shm` ignore so a stray test/migrate run with the
  default `DB_PATH` can't leave an in-repo SQLite file again.

## Alternatives considered

- **Extend the existing client-detail page instead of a new view.** Rejected — client-detail is
  per-client (one venue), already dense, and edit-oriented; the company view is deliberately a
  group-level, read-only command surface. Keeping them separate keeps each focused.
- **A studio-wide dashboard (all companies at once) first.** Deferred — the per-company view is the
  unit a relationship operator works in; the cross-company forecast (recon slice 3) builds on the
  same roll-up queries and can follow.
- **Compute overage inline rather than reusing `recurring.compute_overage`.** Rejected — reuse
  keeps the single source of truth (snapshot-aware, advisory-only) and avoids a second, drifting
  overage calculation.
