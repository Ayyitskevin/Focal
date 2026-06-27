# Mnemosyne brief — albums worker

**Role:** the ALBUMS worker (curated, ordered album-spread layouts). Goal: a stateless proposer
whose layouts pass Mise's deterministic validator AND are provably better than Mise's free
baseline proposer. Conforms to [`../WORKER-CONTRACT.md`](../WORKER-CONTRACT.md) +
[`../../schemas/albums.schema.json`](../../schemas/albums.schema.json).

**Correctness guardrail (every step):** a layout must NEVER silently omit, duplicate, or
misassign a photo. Mise re-validates and rejects malformed layouts — be correct at the source.

**Urgency:** lower than Argus/Plutus — the album capability isn't armed in Mise production yet.
Do this when you want albums live. The Mise-side shadow/adopt seam (flag the baseline →
Mnemosyne) is Mise's job, not Mnemosyne's.

---

## Mode A — umbrella (self-sequencing)

````
You are improving the **Mnemosyne** repo to fit the vision of "Mise Solo Studio OS." Do a
review → gap-analysis → prioritized plan → incremental draft PRs. Deliver the audit and plan
first; wait for go before large changes.

Mise owns galleries, assets, and the album review/approve workflow. Mnemosyne is the ALBUMS
worker: it proposes a curated, ordered subset of a gallery's photos laid into spreads. It must
be a stateless, contract-true PROPOSER — Mise's deterministic validator is authoritative and a
human approves every layout before print/export. Read Mise's existing per-photo signals
(hero_potential, keeper_score); don't recompute vision or duplicate media. Note: Mise already
ships a deterministic BASELINE proposer — Mnemosyne is only worth adopting if its layouts beat
it.

CRITICAL — correctness: a layout must NEVER silently omit, duplicate, or misassign a photo.

Bring Mnemosyne into alignment:
1. Structured output: {"placements":[{"asset_id":<int>,"spread":<int>=0>,"slot":<int>=0>}],
   "provider","model","notes"}. Every placement references a photo+ready asset of THIS gallery;
   each asset placed at most once; each (spread, slot) unique. Omissions allowed but surfaced.
2. Use Mise's hero/keeper signals; no media duplication.
3. Provenance + cost: model, latency_ms, cost_usd (0 for local).
4. Idempotency: stable proposal per (gallery, request).
5. Stateless / retire-ready: cache only; RETIRE.md.
6. Human-approved, never auto-print.
7. Resilience & CI: failures recorded; /healthz; mock-only / reproducible CI; outputs
   deterministic enough to validate against the schema.

Process: claude/... branches; draft PRs a human merges; backward-compatible; independently
green; no secrets; no live calls in CI.

First response: gap analysis vs the 7 points; ranked plan; how proposals reference Mise's
gallery/asset ids + signals — then wait for go.
````

---

## Mode B — discrete sequence

### #1 — Conform: valid placements, validator, signals, idempotency
````
Make Mnemosyne emit strict JSON: {"placements":[{"asset_id":<int>,"spread":<int>=0>,
"slot":<int>=0>}],"provider":"...","model":"...","notes":"optional"}. Every placement must
reference a photo+ready asset of THIS gallery; each asset placed at most once; each (spread,
slot) unique; omitting eligible photos is allowed but must be surfaced, never silent. Read
Mise's per-photo hero_potential/keeper_score signals rather than recomputing vision; don't
duplicate originals. Add provenance (model/latency_ms/cost_usd, 0 for local) and idempotency
(stable proposal per gallery/request). Output is a proposal in review state — nothing prints.
Plan first; draft PR on a claude/ branch; mock-only CI; wait for go.
````

### #2 — Layout quality + beat-the-baseline evidence  *(the reason to adopt it)*
````
Mise already ships a deterministic baseline album proposer; Mnemosyne is only worth adopting if
its layouts are better. Improve layout QUALITY using Mise's signals (hero_potential,
keeper_score): favor keepers (surface, don't silently drop, omissions); sequence a narrative/
chronological arc with a strong opener + closer; group related shots and VARY photos-per-spread
(not a flat N); give heroes room; pick a sensible cover. If model-assisted, use a configurable
LOCAL endpoint, temperature 0, strict JSON — but the model only PROPOSES; deterministic code
must validate AND repair so the result never omits/duplicates/misassigns (reject if it can't be
made valid). THEN add an evaluation harness that, for a gallery, produces both the baseline and
the Mnemosyne layout and emits a structured comparison a human can score (coverage, hero usage,
spread balance, ordering rationale) — targeting "reviewable in <30 min, >=~70% placements
acceptable." Acceptance: on a fixed set, Mnemosyne is rated >= baseline; always passes the
validator; deterministic given the same input+seed; fully local. Plan + eval design first;
draft PRs; wait for go.
````

### #3 — Statelessness / no media duplication + RETIRE.md + /healthz + CI
````
Make Mnemosyne stateless and retire-ready: no media duplication, no second run-store of
authority — a cache + reproducible outputs only. Add RETIRE.md (what Mise owns, what's safe to
turn off, rollback). Expose /healthz. Ensure CI is mock-only / reproducible and add tests that
validate output against the albums schema AND assert validator-conformance (no omit/dup/
misassign) on representative fixtures. Plan first; draft PR; wait for go.
````
