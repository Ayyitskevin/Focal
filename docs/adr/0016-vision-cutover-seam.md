# ADR 0016 — Vision cutover seam: interlocked production-provider selection

**Status:** Accepted (vision promotion mechanism — interlock + designation)
**Date:** 2026-06-26
**Deciders:** Kevin (owner), principal engineer

> **Update (2026-06-28):** the deferred slice (b) below — *routing the live vision trigger
> through the facade seam* — is now done. The production analyze job (`jobs._h_vision_analyze`,
> same `argus_analyze_gallery` kind) dispatches via `registry.active_vision_provider()`: Argus
> by default, the Qwen production writeback only once Qwen is the *eligible* provider. It stays
> byte-identical to before until a deliberate promotion (`serves_production=True` +
> `MISE_VISION_PROVIDER=qwen`), so cutover is now a flag flip, not a delivery-path edit. The
> remaining slice (a), the Qwen writeback path, already exists (ADR 0017); what's left is live
> prompt-tuning + the `serves_production` flip against a real endpoint.

## Context

The vision evaluation chain is complete (shadow → ledger → validation gate, ADRs
0006/0007/0010/0014): the gate computes whether the Qwen3-VL challenger has reached
parity-or-better with Argus on a human-scored validation set. But a green gate **advises**;
nothing acts on it. The intended payoff — promoting the challenger to serve production
vision — had no mechanism.

Tracing the code surfaced two facts that shape what a *safe* mechanism is:

1. **Production vision does not flow through the facade.** `app/admin/galleries.py` and the
   `argus_analyze_gallery` job call `app.argus_analyze` directly; `resolve(VISION)` is only
   exercised by tests and the shadow harness. (Offers and content *were* strangled through
   the facade; vision never was.)
2. **The challenger has no production writeback.** `InternalVisionChallengerAdapter`
   returns a raw reply for the shadow ledger (ADR 0007), not the structured
   keywords/alt-text/keeper/hero writeback Argus performs.

Therefore a bare `MISE_VISION_PROVIDER=challenger` flag would be a **footgun**: it would
route production galleries into a path that writes no asset metadata.

## Decision

Build the selection seam **with a hard interlock**, and explicitly do **not** rewire the
live trigger in this slice.

- **`MISE_VISION_PROVIDER`** (default `argus`) names the production vision provider.
- Adapters declare **`serves_production`**: `LegacyArgusVisionAdapter` = True (owns the full
  trigger→callback→writeback path); `InternalVisionChallengerAdapter` = False (eval-only
  until a writeback path exists).
- **`registry.active_vision_provider()`** resolves the requested provider with the
  interlock: it is honored only if it is known, declares `serves_production`, **and** is
  configured/enabled; otherwise it **falls back to Argus** and returns the reason. A flag
  pointing at the eval-only challenger (or an unknown/unconfigured provider) can never
  silently route production into a non-writeback path.
- The validation gate surfaces the **effective production provider** and, when a request
  isn't honored, why — making the gate's "promote manually" note concrete.
- **The live trigger is intentionally left calling Argus directly.** Wiring it to consult
  `active_vision_provider()` is deferred until there is a production-capable challenger to
  route to (see below) — there is nothing safe to switch to today, so adding the switch into
  the live path now would only add risk.

## Consequences

- **Positive:** the cutover switch and its safety interlock exist, are tested, and are
  operator-visible, with Argus as the permanent default/fallback. When the challenger
  becomes production-capable, promotion is: register it as production-capable → wire the
  trigger through the seam → flip one reviewed flag → rollback = flip back. The interlock
  guarantees a misconfiguration degrades to Argus, never to a broken path. Behavior today is
  unchanged (effective provider is always Argus). No schema, trivial rollback.
- **Honest scope:** this is the *interlock + designation*, not a live flip. The remaining
  work to make the flag actually switch production is two reviewed slices: (a) a Qwen
  **production writeback** path producing structured asset signals, gated on the validation
  data justifying the investment; (b) routing the live vision trigger through the facade
  seam. Both are deliberately out of scope here.

## Alternatives considered

- **A plain `MISE_VISION_PROVIDER` flag that routes immediately.** Rejected — a footgun
  given the challenger has no writeback; it would silently stop asset metadata from being
  written. The interlock is the whole point.
- **Rewire the live gallery trigger through `resolve(VISION)` now.** Rejected for this slice
  — it touches the sensitive gallery-delivery path for no present benefit (there's no
  production-capable alternative to route to). It is the right step *after* the challenger
  writeback exists.
- **Auto-promote when the gate goes green.** Rejected — promotion of client-facing metadata
  quality stays a human decision (ADR 0010); the gate informs, a human flips.
