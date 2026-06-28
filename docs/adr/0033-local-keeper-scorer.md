# ADR 0033 — Local keeper-scorer for culling (per-asset Qwen scores into the deck)

**Status:** Accepted (extends the culling arc 0030–0032; the "local/on-device AI" leg)
**Date:** 2026-06-28
**Deciders:** Kevin (owner), principal engineer

## Context

The cull deck (ADR 0031) ranks on `argus_keeper_score`. That column is written by the cloud Argus
path or the (dormant, cutover-locked) Qwen production writeback. Two gaps motivated this:

1. The operator's stated direction is **local/on-device AI** — they want scores from a local model,
   not a cloud sidecar.
2. The existing local path is **broken at scale**: `vision_challenger` / `qwen_writeback` send up to
   `MISE_VISION_CHALLENGER_MAX_IMAGES` (default **4**) photos, base64-inlined, in **one** call, and
   match results back to assets by **basename**. That's fine for a shadow spot-check, useless for
   scoring a whole shoot — most frames come back `unscored`, so the deck's ranking and threshold
   sweep (its two time-savers) do nothing.

This ADR adds a purpose-built local scorer that fills the score column for the deck, **without**
promoting Qwen to be the production vision provider (which is a separate, heavier decision —
keywords/alt-text/IPTC on publish, the cutover interlock of ADRs 0016/0017).

## Decision

A new `app/cull_scorer.py` — score-only, per-asset, asset_id-keyed, with its own flag.

- **Per-asset, not batched-inline.** Each photo is scored in its **own** call (one image in, one
  float out) over **every** ready photo — no `MAX_IMAGES` cap. There is no cross-photo basename
  matching, so a malformed reply drops a single asset, never a batch, and the write is keyed by
  `asset_id` by construction. Local inference has no per-call cost, and it runs as a background job,
  so the extra calls are free and off the request path.
- **Score-only.** It writes **only** `argus_keeper_score` (validated to `[0,1]` by a deterministic
  parser that tolerates prose/fence wrapping and rejects anything else). It never touches
  keywords / alt-text / hero — so it can't clobber whatever Argus (or nothing) set. The deck reads
  the score source-agnostically, so cloud and local writers coexist in the same column.
- **Its own flag, decoupled from the cutover.** `MISE_CULL_SCORER` (+ a `MISE_VISION_CHALLENGER_URL`
  endpoint) arms it. It is **independent** of `registry.active_vision_provider()` — using local
  scores to cull does not require, and does not perform, the Argus→Qwen production cutover. The
  dormant `qwen_writeback` path (ADR 0017) is untouched.
- **Trigger:** a **Re-score with local AI** button on the cull deck → `POST …/cull/rescore` enqueues
  a `cull_score_gallery` job. The route is gated by the deck flag (404 when culling is off) and
  returns a clear **503** when the scorer itself isn't armed. Background; the operator refreshes the
  deck to see scores.
- **Provenance:** one `ai_runs` row per run (capability=vision, provider=qwen3-vl, cost 0, the
  scored/failed/total counts), honouring ADR 0006 without a row-per-photo flood.

## Consequences

- **The deck's AI-assist works locally and at scale** — a whole gallery gets scored, on the
  operator's own model, which is the local-first direction they chose.
- **§11.4 holds:** the model only proposes a *ranking* score; every keep/cut is still a human
  keystroke in the deck, and the scorer writes nothing client-facing (the delivery gate, ADR 0032,
  is the only thing that hides frames, and only on an operator cut).
- **No entanglement with the cutover:** scoring for culling and replacing Argus as the production
  vision provider stay separate concerns with separate flags, so neither forces the other.
- **Dormant until armed:** off by default; with the flag off (or no endpoint) the trigger 503s and
  nothing calls the model. Rollback is the flag.
- **Cost of per-asset calls:** more round-trips than one inline batch, but local + background, so
  latency is invisible and cost is zero. If throughput ever matters, true batching is an
  optimisation behind the same interface — not needed now (logged, not silently assumed).

## Alternatives considered

- **Fix `qwen_writeback`/`vision_challenger` to score all photos in big inline batches.** Rejected —
  multi-image base64 in one call is memory-heavy and brittle at gallery scale, and it re-introduces
  basename matching; per-asset is simpler and correct. The batch path stays as the shadow/preview
  spot-check it was built for.
- **Reuse the production cutover (promote Qwen) to get scores.** Rejected — that's a far bigger,
  heavier decision (it changes production keywords/alt-text/IPTC and needs the validation gate); the
  operator just wants scores for culling. Decoupling keeps each choice independent.
- **Write keeper_score + keywords + alt-text from the scorer.** Rejected — would clobber Argus's
  production metadata; the cull scorer's job is the ranking score and nothing else.
- **Score synchronously from the deck.** Rejected — scoring a full gallery is long; a background job
  keeps the deck responsive and survives a page close.
