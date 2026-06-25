# ADR 0004 — Odysseus / model-provider boundary

**Status:** Accepted (Phase 0)
**Date:** 2026-06-25

## Context

Odysseus is the external reasoning and model-routing layer. Mise already delegates
caption-model choice to it **[CODE]** (`app/caption_ai.py`: Mise POSTs context, takes
back `{caption, model}`; Odysseus owns the prompt and model). Argus/Plutus are external
vision/offers providers. We need a boundary that lets providers *propose* without ever
letting a model *decide* money, contracts, publication, or delivery.

## Decision

**Odysseus (and any provider/model) is an adapter behind the Mise `providers` seam.**
Business rules and authoritative state stay in Mise. Providers may **propose structured
outputs**; they must **not** directly mutate money, contract, gallery-publication, or
client-delivery state. Concretely:

- Mise requests a **capability/quality tier**, not a specific model name; the provider
  (Odysseus/gateway) owns routing (audit §9.6).
- Every provider call returns a normalized `ProviderResult` (provider, model, status,
  latency, cost-when-available, **review requirement**). Only an `OK` result is eligible
  to drive a downstream write, and only through **deterministic Mise code + human
  approval** for anything client-facing or money/legal (approval classes A0–A4, audit
  §11.4).
- Provider failure is separated from business-state failure: timeouts/outages return a
  non-`OK` result and mutate nothing.
- Heavy execution may run on mickey/strix/cloud; those workers are replaceable and own
  no business records (ADR 0001).

## Consequences

- **Positive:** the model backend can change (external Argus → cloud → local worker)
  without touching workflows; central policy/cost/provenance; safe failure.
- **Negative:** a thin contract layer to maintain (`app/providers/`) — justified because
  it removes real per-sidecar duplication and is the migration seam (Mise rule R2).
- **Rule:** no code path lets an external model set price, payment state, contract
  state, publication, deletion, or "send."

## Alternatives considered

- **Direct model-specific calls from each module:** rejected — couples every caller to a
  provider, duplicates retries/policy, no shadow seam (audit §8 "Cloud adapters").
- **Let Odysseus write Mise state directly:** rejected — violates ADR 0002; Odysseus→Mise
  is a narrow command API with approval class + idempotency + version precondition only
  (audit §11.3).
