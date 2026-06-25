# ADR 0006 — AI provenance and human approval

**Status:** Accepted (Phase 0)
**Date:** 2026-06-25

## Context

AI outputs touch client metadata, captions, offers, album layouts, and (later) generated
images. The audit requires explicit schemas for provider responses, validation/normal-
ization before output enters domain logic, recorded provenance, and human authority over
anything client-facing or money/legal (audit §8.3, §9.4, §11.4, §13.4). Today Mise
records provenance ad hoc per capability (`galleries.argus_last_*`, `plutus_last_*`,
`retainer_captions.ai_model/ai_drafted_at`, `assets.argus_*`) **[CODE]** — useful but
non-uniform, with no single review queue or shadow-comparison record.

## Decision

1. **One normalized result type** for every AI capability — `ProviderResult` **[CODE,
   Phase 0]** — carrying `capability`, `provider`, `status`, `review`, `output`, `model`,
   `latency_ms`, `cost_usd`, `tokens`, `error`, plus a flat `provenance()` record.
2. **Validate before domain logic:** providers return explicit, normalized output; an
   empty/unparseable/schema-invalid response becomes `INVALID_RESPONSE` (non-mutating),
   not a silent partial write.
3. **Human approval is mandatory** for AI-generated client communication, image
   selection, album design, pricing, and offers — they are **drafts** (`ReviewRequirement`
   `HUMAN_REVIEW`/`EXPLICIT_COMMIT`, mapping to audit approval classes A1/A3/A4). No
   model auto-publishes, auto-prices, auto-sends, or auto-deletes.
4. **Only an `OK` result may drive a write**, and only via deterministic Mise code. A
   `DISABLED`/`PROVIDER_ERROR`/`INVALID_RESPONSE` result mutates nothing — provider
   failure is separated from business-state failure.
5. **Persisted provenance is a future additive table** (`ai_runs`, roadmap Phase 1.1) —
   designed now, **not** migrated in Phase 0 (no schema change this slice).

## Consequences

- **Positive:** a single review queue and shadow-comparison record become possible;
  every output is attributable (provider/model/version/latency/cost/reviewer); failures
  are safe by construction.
- **Negative:** in-memory-only provenance until Phase 1.1 ships the table (a red-light
  migration). Cost/tokens stay `None` for providers that do not report them — the field
  exists for the internal adapters the roadmap adds.
- **Bias/error monitoring (audit §13.4):** culling/hero scores are reviewed across skin
  tones, group compositions, and event types; aesthetic score is never treated as
  objective truth; face/identity recognition stays disabled.

## Alternatives considered

- **Keep ad-hoc per-capability provenance:** rejected — no shared review/shadow seam,
  drift across modules.
- **Persist provenance now:** rejected for Phase 0 — it requires a schema migration
  (red-light); designed in the roadmap and deferred.
