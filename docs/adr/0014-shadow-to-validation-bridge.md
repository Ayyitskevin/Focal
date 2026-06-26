# ADR 0014 — Shadow→validation bridge: enrol shadowed galleries into the gate from the ledger

**Status:** Accepted (validation workflow ergonomics)
**Date:** 2026-06-26
**Deciders:** Kevin (owner), principal engineer

## Context

The vision evaluation loop has all its parts (ADRs 0006/0007/0010): shadow mode records a
legacy-vs-challenger pair to `ai_runs` per analyzed gallery, and the validation gate
computes a promotion verdict from human scores over a fixed validation set. But the two
ends were not connected in the UI. To score a shadowed gallery, the operator had to *know*
which galleries shadow mode had compared and **re-type each gallery id** into the
validation set by hand. That friction is the practical reason the gate stays at "not
ready" — not a lack of data, but a lack of an obvious path from "shadow ran" to "score it."

## Decision

Surface the bridge in the validation UI: discover galleries that have shadow runs in the
ledger but are not yet enrolled, and let the operator enrol one with a single click.

- **`validation.shadow_candidates(capability)`** (read-only): galleries with `ai_runs` rows
  for the capability whose `correlation_id` marks a shadow run (`shadow:gallery:…`) and that
  have no active `validation_items` row yet. Returns gallery + shadow-run count + last
  shadow time, newest first.
- **`/admin/validation`** renders a "From vision shadow runs" section listing those
  candidates; each row's "Add to set" button POSTs to the **existing** add-item route
  (prefilled `subject_type=gallery`, the gallery id, and its title as the label). After
  enrolment the candidate drops off the list and appears in the set, ready to score.
- **No new write path, no schema.** It reuses `validation.add_item` and reads `ai_runs` +
  `validation_items`. Enrolment is still a deliberate human click — nothing is auto-added,
  auto-scored, or promoted.

## Consequences

- **Positive:** removes the main friction from the workflow the vision cutover depends on —
  "what should I score next?" is now answered by the ledger instead of the operator's
  memory. Tightens the loop the architecture already implies (shadow → ledger → enrol →
  score → gate) without new state. Trivial rollback (drop a helper + a template section).
- **Negative / bounded:** it lists *candidates*, not an auto-enrolled set — by design, so
  the validation set stays a curated, deliberate thing (the audit's "fixed validation set",
  §9.5). It does not yet link a specific `ai_runs` row to the score it informs
  (`record_score` accepts `ai_run_id`, but wiring per-run scoring through the UI is a later
  refinement). Candidates are scoped to galleries with a `shadow:%` correlation id, so a
  capability without shadow provenance simply shows none.

## Alternatives considered

- **Auto-enrol every shadowed gallery.** Rejected — the validation set is meant to be a
  *curated* benchmark, not "everything that happened to run." Auto-enrolment would dilute it
  with whatever galleries were analyzed and make the mean quality reflect convenience rather
  than a representative set. Discovery + one-click keeps curation human.
- **A separate "shadow review" page.** Rejected — the decision (score it / don't) belongs on
  the validation page next to the set and the gate verdict, not on a third surface the
  operator has to cross-reference.
- **Link scoring directly to the `ai_runs` pair (skip the validation item).** Deferred — the
  gate aggregates over the *validation set*, so enrolment-then-score keeps one consistent
  basis; per-run `ai_run_id` linkage is an additive refinement on top, not a replacement.
