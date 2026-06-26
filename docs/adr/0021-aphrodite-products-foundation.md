# ADR 0021 — Aphrodite products: a budget-capped, export-gated foundation (dormant)

**Status:** Accepted (Phase 6 — the last sidecar capability, built dormant)
**Date:** 2026-06-26
**Deciders:** Kevin (owner), principal engineer

## Context

Aphrodite (product-image generation) is the last sidecar and the most sensitive: it
*generates* imagery, which costs money and raises copyright/consent questions. The roadmap
(Phase 6) and audit (§13.5) gate it hard — budget-capped, no automatic client publication,
explicit human approval, consent/licensing review, export-gated — and make it optional, only
if there's commercial value. We build the **foundation now** (the deterministic guards +
schema + a dormant capability seam) so the safety floor exists and is tested before any image
is ever generated, exactly as ALBUMS started (ADR 0009). The business specifics — the render
backend, the variant catalogue, the budget number, the written consent/licensing policy —
are deliberately deferred to activation.

## Decision

Add `Capability.PRODUCTS`, `app/products.py` (the deterministic guards), `product_jobs`
(migration 071), and a dormant `ProductsRenderAdapter` — all inert by default.

- **Budget cap (deterministic).** Total spend is hard-capped by `config.PRODUCTS_BUDGET_USD`
  (default 0). `products.create_render` refuses any render that would push cumulative
  `cost_usd` over the cap, and refuses everything when the capability is disabled — so a
  runaway or misconfigured backend cannot spend past the ceiling.
- **No automatic publication.** A render is a `draft` in HUMAN_REVIEW; nothing here publishes.
- **Consent + export gate.** `export_job` — the single outbound step — refuses unless the job
  is `approved` AND `consent_confirmed`. Rights/consent (§13.5) is a structural precondition,
  enforced in code before the policy prose even exists.
- **Dormant by construction.** `is_enabled()` requires both a render URL and a positive
  budget; both default off, so `create_render` refuses everything and nothing in the running
  app calls the module. The `ProductsRenderAdapter` declares `serves_production = False` and
  has no wired backend (its `render` returns a non-OK result), mirroring the vision-challenger
  interlock pattern.
- **Provenance.** A recorded render writes an `ai_runs` row (capability `products`, with
  `cost_usd`) best-effort, so the AI cost report sees product spend the moment it's armed.
- **Schema.** Migration 071 adds one additive `product_jobs` table; rollback drops it.

## Consequences

- **Positive:** the safety-critical parts — the spend ceiling, the no-auto-publish rule, the
  approval+consent export gate — exist and are tested before a single image is generated.
  Arming products later is wiring a backend behind the adapter and setting a budget + policy,
  not building the guards under time pressure.
- **Provably inert:** defaults disable it; `create_render` refuses; the adapter renders
  nothing; nothing calls the module. Red-light migration shipped for human merge.
- **Deferred to activation (owner's calls):** the actual render backend/worker, the variant
  catalogue (what a "product" is), the budget number, and the written consent/licensing
  policy. The foundation enforces *that* consent is confirmed; *which* policy is the owner's.

## Alternatives considered

- **Wait and build products only when activating.** Rejected — the guards (budget, consent,
  export) are exactly what must not be rushed when money and rights are live; building them
  dormant first is the whole point, and matches how every other capability started.
- **Make the budget advisory / log-only.** Rejected — the audit wants a hard spend guard; an
  advisory cap is a footgun for a capability that spends per call.
- **Skip the capability adapter for now.** Rejected — wiring `Capability.PRODUCTS` into the
  facade keeps products consistent with the strangler seam every other capability uses.
- **Model consent as free text only.** Rejected for the gate — a boolean `consent_confirmed`
  the export check enforces is what makes "no use without cleared rights" deterministic; notes
  can still live in `spec`.
