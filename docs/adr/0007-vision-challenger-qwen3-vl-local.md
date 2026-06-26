# ADR 0007 — Vision challenger: Qwen3-VL on a local OpenAI-compatible endpoint

**Status:** Accepted (Phase 2 backend)
**Date:** 2026-06-26
**Deciders:** Kevin (owner — "use the model you rec"), principal engineer

## Context

Phase 2 shipped the vision shadow harness (asset-safe, ledger-only) with a challenger
*seam* but no backend, so shadow was inert. To produce the measured local-vs-cloud
quality/cost data the audit calls for (§9.5), a real challenger must run alongside the
legacy Argus path. Current production vision is **xAI Grok via Argus** (cloud) **[CODE]**.

The audit's named challenger family (§9.2) is **Qwen3-VL**: an 8B-class tier (~7–11 GB) and
a premium 30–32B tier (~22–28+ GB, "only ≥32 GB practical memory"), valued for "private
captioning, metadata, album analysis" and explicitly required to "beat the Argus validation
set" before any promotion. The audit also recommends exposing local models behind a
**stable OpenAI-compatible endpoint**, with **Ollama** as the starting layer (§9.6), so
callers depend on a capability/quality tier rather than a specific runtime.

Kevin runs **`qwen3-vl:32b` on `mickeybot`** (deployed evidence — authoritative). That
hardware/model choice is provisioned, so this ADR records the **premium 32B tier on
mickeybot** as the challenger, not the smaller starting tier I initially defaulted to.

## Decision

Adopt **Qwen3-VL (32B) served on `mickeybot` via Ollama's OpenAI-compatible API** as the
internal vision challenger (default model `qwen3-vl:32b`, overridable by env). Implemented
as `InternalVisionChallengerAdapter` (`app/providers/vision_challenger.py`), conforming to
`Capability.VISION`:

- **Dormant by env.** Disabled unless `MISE_VISION_CHALLENGER_URL` is set; with both that
  and `MISE_VISION_SHADOW` armed, shadow runs for real. No secret is stored in the repo —
  only the key names are documented in `.env.example`.
- **Local-only by posture.** The URL must point at a trusted local endpoint; a cloud
  vision default is intentionally unsupported here. Privacy-positive vs. the status quo
  (Argus already sends to cloud), and it sends downsized **web derivatives** (not RAW),
  capped at `MISE_VISION_CHALLENGER_MAX_IMAGES` (audit §13.4 data minimization).
- **Shadow-only, ledger-only, human-reviewed.** Results are recorded to `ai_runs` for
  comparison (`providers.shadow.compare`) and surfaced at `/admin/ai-runs`; they are
  **never** written to assets/galleries and never auto-promoted. Human review against the
  validation set is the promotion gate.
- **Premium quality tier on provisioned hardware.** `qwen3-vl:32b` is the model Kevin runs
  on `mickeybot`; the validation set (not model size) remains the promotion gate, and the
  env override allows dropping to a smaller tier if the cost/latency ledger argues for it.

## Consequences

- **Positive:** turns the shadow harness into real local-vs-cloud data; private (local +
  derivatives only); reversible (two flags); provider-agnostic via the OpenAI-compatible
  seam so the runtime/model can change without touching callers.
- **Negative / to validate:** the prompt and response parsing are written to the
  OpenAI-compatible contract but **not yet validated against a live endpoint** — the
  shadow ledger + human scoring (§9.5) is exactly the mechanism to tune them before any
  promotion. Until then the adapter stores the raw reply for review, not a strict schema.
- **Latency to measure (audit §4):** the 32B tier needs ample VRAM and is heavier than the
  8B tier; whether `mickeybot` serves it within an acceptable shadow window is exactly what
  the shadow latency in `ai_runs` measures.

## Promotion gate (unchanged from the roadmap)

Promote the challenger over Argus only after **human-scored parity on the fixed
validation set + a cost/latency ledger + an observation period** (roadmap Phase 2.4,
audit §9.5). Argus stays the default and the legacy adapter is never removed until then.

## Alternatives considered

- **Cloud vision challenger (another provider):** rejected as the default — sends client
  media to a second cloud provider; the local option is the privacy-positive, lower-cost
  path the audit recommends. Still reachable later behind the same seam if a measured case
  emerges.
- **Smaller Qwen3-VL 8B-class:** available via the env override and lower-footprint, but
  Kevin has provisioned the 32B tier on `mickeybot` for quality; if the cost/latency ledger
  shows 32B isn't worth it, dropping to 8B is a one-env-var change behind the same seam.
