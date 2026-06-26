# ADR 0010 — Validation-scoring harness: a deterministic promotion gate

**Status:** Accepted (Phase 2.4 — the gate the shadow ledger feeds)
**Date:** 2026-06-26
**Deciders:** Kevin (owner), principal engineer

## Context

Phase 2 (ADR 0007) shipped the vision shadow harness: a challenger (Qwen3-VL on mickeybot)
runs alongside the legacy Argus path and both outcomes are recorded to `ai_runs`. The
roadmap and the audit (§9.5) are explicit that a challenger may be promoted over the
incumbent **only after "human-scored parity on the fixed validation set + a cost/latency
ledger + an observation period."** Until now Mise had the *ledger* but not the *decision*:
comparison rows accumulate with no objective, repeatable answer to "is the challenger good
enough yet?" Without that, a cutover would be a vibe, and the legacy path would either
never be retired or be retired unsafely.

The judgement of *quality* is irreducibly human (does this caption / these keywords / this
culling actually serve the client?). What should be deterministic is everything around that
judgement: which subjects we evaluate on, how scores aggregate, and what threshold counts
as "ready." That is the same division this codebase already applies elsewhere — the model
proposes, deterministic code decides (ADR 0009 for albums; §11.4).

## Decision

Add a **validation-scoring harness** whose gate verdict is computed by deterministic code
from human-entered quality scores.

- **Fixed validation set** (`validation_items`, migration 067): curated subjects
  (gallery/asset) per capability, with an optional human-authored ground-truth note.
  `UNIQUE(capability, subject_type, subject_id)` keeps the set stable and dedup'd.
- **Human scores** (`validation_scores`): one quality score in **[0, 1]** per
  `(item, model)`, optionally linked to the exact `ai_runs` row scored.
  `UNIQUE(item_id, model)` + an upsert in `record_score` means re-scoring overwrites rather
  than double-counting. Out-of-range scores raise rather than silently store.
- **Deterministic verdict** (`app/validation.build_report`, pure / no I/O): over the items
  scored for **both** baseline and challenger (the only fair basis), compute the mean
  quality delta and head-to-head W/T/L. `ready` is True iff **paired coverage ≥
  `MISE_VALIDATION_MIN_PAIRED`** (default 20) **AND** the mean delta **≥
  `MISE_VALIDATION_PARITY_MARGIN`** (default 0.0 = parity-or-better). Cost and latency are
  pulled from `ai_runs` and shown, but are **informational** — per the audit they inform the
  human decision and never flip `ready` on their own. Every criterion is spelled out in
  `reasons` so the verdict is auditable, not a bare boolean.
- **Read-only surface** (`/admin/validation`): renders the set, per-model means, the
  ledger cost/latency, and the verdict; CSV export of the scores. It does **not** promote
  anything and (this slice) does not enter scores.

## Consequences

- **Positive:** the cutover criterion is now objective, repeatable, and tunable by env
  (no code change to retighten the gate). The quality signal stays human; the arithmetic
  and threshold are deterministic and unit-tested. Pairing on shared items avoids the
  classic "challenger looks better because it was scored on easier cases" bias. Rollback is
  dropping two empty tables.
- **Negative / deferred:** **scoring data entry is not in this slice** — scores are recorded
  via `validation.record_score(...)` (and tests), not a UI. A scoring form is a deliberate
  follow-up (it introduces an admin write path; kept separate to stay clear of the
  CSRF/auth red line). The gate also presumes the human scores honestly and consistently;
  it measures agreement with a human judge, not ground truth in the abstract.
- **Promotion is still manual.** Even a green verdict does not flip any provider — vision
  has no challenger-cutover flag yet; adding one is a future, separately-reviewed change.
- **Schema:** `validation_items` / `validation_scores` are new → the red-light migration
  that gates this slice behind a human-merged PR.

## Alternatives considered

- **Auto-promote on a green metric.** Rejected — it removes the human from a client-facing
  quality decision the audit explicitly keeps human, and a gameable metric would eventually
  promote a worse model. The gate advises; it never acts.
- **Score challenger absolutely (no baseline pairing).** Rejected as the basis for `ready` —
  absolute scores drift with case difficulty and scorer mood. Paired deltas against the
  incumbent are the comparison that actually answers "promote or not." (Absolute means are
  still shown for context.)
- **Make cost/latency hard gates.** Rejected — a cheaper/faster model that is *worse* must
  not pass. Cost/latency are decision inputs surfaced to the human, not auto-gates.
- **Store scores as free text / stars without a fixed [0,1] range.** Rejected — the gate
  must aggregate deterministically; a bounded numeric scale with range-checking is what
  makes the mean and delta meaningful.
