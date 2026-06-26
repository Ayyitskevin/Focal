# ADR 0017 — Qwen vision production-writeback (dormant scaffold)

**Status:** Accepted (vision cutover — the writeback half, built dormant)
**Date:** 2026-06-26
**Deciders:** Kevin (owner), principal engineer

## Context

The vision cutover seam (ADR 0016) gives the operator a flag to designate the production
vision provider, with a hard interlock: a provider may only serve production if it declares
`serves_production`. The Qwen challenger declares `False` because it had no way to produce
the structured per-photo signals Argus writes — it only returned a raw reply for the shadow
ledger (ADR 0007). So the gate could go green, but there was nothing to promote *to*.

This ADR adds that missing half — the structured analysis + asset writeback — **now**, so
it is written, validated, and reviewed before the data says "promote", while keeping it
**provably inert** until promotion is a deliberate act.

## Decision

Add `app/qwen_writeback.py`, mirroring `argus_writeback`, built dormant:

- **`parse_structured`** — the deterministic validator (audit §11.4, "model proposes,
  deterministic code validates"). It turns the model's reply (strict JSON, tolerant of
  prose/code-fence wrapping) into normalized photo dicts and **rejects** anything malformed
  or out-of-range (`keeper_score`/`hero_potential` must be floats in [0,1]; keywords a list
  of strings; basename required). A bad proposal never reaches the writeback.
- **`apply_to_gallery`** — the deterministic writeback. Identical contract to
  `argus_writeback.apply_to_gallery`: match by basename to **photo+ready** assets of *this*
  gallery only, write the same `argus_*` columns (role-named for vision, read across the
  app), recompute the gallery hero set, idempotent. A promoted Qwen is therefore a true
  drop-in — no schema change, no consumer change.
- **`writeback_gallery`** — the orchestrator, **self-interlocked** on
  `registry.active_vision_provider()`: it writes nothing and returns `{"skipped": True}`
  unless Qwen is the *eligible production* provider. With the challenger at
  `serves_production = False` today, it is a guaranteed no-op. It never raises
  (background-job-safe).
- The endpoint plumbing is shared with the shadow adapter via
  `vision_challenger.chat_completion` (extracted, behavior-identical).

## Consequences

- **Positive:** the whole vision cutover is now *built*. The day the validation data earns a
  promotion, flipping it is a bounded change — tune the prompt/parsing against the live
  endpoint, set `serves_production = True`, wire a trigger to `writeback_gallery`, and set
  `MISE_VISION_PROVIDER=qwen`. The deterministic validator + writeback are already correct
  and tested. Rollback stays the flag.
- **Provably dormant:** nothing in the running app calls `writeback_gallery`, and even a
  direct call is refused by the interlock until Qwen is promoted, so the scaffold mutates no
  asset today. `apply_to_gallery` is reachable as a building block (and is what tests
  exercise), but only the gated orchestrator wires it to the live model.
- **Honest gap:** `STRUCTURED_PROMPT` and the parse mapping are written to a reasonable
  schema but **not yet validated against a live Qwen endpoint** — that tuning (and the
  trigger wiring) is the remaining, separately-reviewed step. Until then the column reuse
  means a promoted Qwen writes the `argus_*`-named columns; renaming them to `vision_*` is a
  larger, optional follow-up touching many consumers and is out of scope.

## Alternatives considered

- **Wait for the data before building any writeback.** Rejected — the writeback structure
  (validate → match → write, idempotent, asset-safe) is sound regardless of the data; only
  prompt tuning depends on it. Building it now de-risks the cutover and means the green-gate
  day is a flag flip, not a build.
- **Have Qwen write its own `vision_*`/`qwen_*` columns.** Rejected for the drop-in path —
  the rest of the app reads `argus_*`; a promoted provider should populate those so nothing
  downstream changes. A rename is a separate, app-wide refactor if ever wanted.
- **Drop the interlock and gate only by a flag.** Rejected — same footgun ADR 0016 avoided;
  the `serves_production` interlock makes the scaffold inert by construction, not just by
  configuration discipline.
