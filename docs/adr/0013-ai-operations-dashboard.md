# ADR 0013 — AI operations dashboard: one read-only pane over the consolidated capabilities

**Status:** Accepted (operations cohesion)
**Date:** 2026-06-26
**Deciders:** Kevin (owner), principal engineer

## Context

The consolidation shipped each AI capability as its own operator surface — the `ai_runs`
provenance ledger (`/admin/ai-runs`), the vision promotion gate (`/admin/validation`), the
offers review queue (`/admin/offers`), and the album drafts (`/admin/albums`). Each is
correct in isolation, but there was no single place that answers the operator's actual
morning question: *across all of this, what needs me today, and how is the AI spend/activity
trending?* Without that, pending work (an undecided offer, a draft album, a not-ready gate,
a run of provider errors) is only visible if you remember to open each queue.

## Decision

Add `/admin/ai-ops`, a **read-only aggregation** page — the "one pane of glass" the Solo
Studio OS arc was building toward.

- **Needs-attention tiles** (triage order): offers awaiting a decision (count + proposed
  value), album drafts to review, the vision promotion-gate verdict (ready / not-ready +
  paired coverage), and provider errors in the ledger. Each tile links to the queue that
  **owns** the action and highlights when it has something pending.
- **Ledger summary**: total runs, runs in the last 7 days, reported provider cost, and a
  per-capability breakdown — read straight from `ai_runs`.
- **Jump links** to the four surfaces.
- It reuses `validation.promotion_report` and reads existing tables only. **No schema, no
  new write path, no external calls, no money** — and nothing on the page decides,
  promotes, sends, or charges. The human-review gate stays in each queue.

## Consequences

- **Positive:** one discoverable surface ties the session's work together and surfaces
  pending action without hunting through four pages — which also nudges the operator to
  actually exercise the review workflows. Cheap to maintain (pure reads), trivially safe to
  roll back (delete a route + template). No migration, so this is a green-light change that
  still ships as a draft PR per the project's review norm.
- **Negative / bounded:** the page is intentionally *not* a control surface — it cannot act,
  by design, so it never becomes a second place where money/publish decisions happen. The
  aggregate queries are unbounded counts/sums over `ai_runs`; at studio scale that is
  trivial, and a retention/rollup policy for the ledger is a separate concern if volume ever
  warrants it.

## Alternatives considered

- **Fold the tiles into the existing `/admin/home`.** Rejected for now — home
  (`admin/activity.py`) is already dense, and a dedicated page keeps the AI-operations
  concern self-contained and discoverable (matching how each capability got its own
  surface). The tiles can be embedded into home later if that proves the better home.
- **Make the dashboard actionable (approve/decide inline).** Rejected — duplicating the
  decision routes here would create two places where the human-review gate lives and risk
  divergence. The dashboard points at the queues; the queues own the actions.
- **Add a new rollup table / cache.** Rejected as premature — the live aggregate queries are
  cheap at single-studio scale; a cache would be complexity without a measured need.
