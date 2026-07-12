# ADR 0068 — AI capability lives in the app's deployable unit

**Status:** Proposed
**Date:** 2026-07-12
**Deciders:** Kevin (owner), full-stack/architecture
**Supersedes (in part):** the target-topology of `docs/MISE-CONSOLIDATION-ROADMAP.md`
(its strangler *mechanism* stands; its per-capability *endpoint* is re-pinned here)

## Context

`docs/MISE-REVIEW.md` §2 established, from the actual adapter code, that **every
live AI capability in Mise calls a separate self-hosted sidecar** — none is
in-process, and none calls a hosted model API directly:

| Capability | Adapter → module | What it actually is | Sidecar |
| --- | --- | --- | --- |
| VISION | `LegacyArgusVisionAdapter` → `app/argus_analyze.py` / `app/argus_writeback.py` | Argus resolves originals itself via a **shared media root** (`ARGUS_MISE_MEDIA_ROOT`), calls a vision model (internally cloud Grok), owns a run store, and calls back async → Mise's `argus_writeback` pulls the run and writes asset scores/alt-text/keywords/hero | `MISE_ARGUS_URL` (+ bearer) |
| VISION challenger | `InternalVisionChallengerAdapter` → `app/providers/vision_challenger.py` | Qwen3-VL (32B) on **the operator's homelab** (`mickeybot`) via an OpenAI-compatible endpoint; eval-only, `serves_production=False`, dormant until env set | `MISE_VISION_CHALLENGER_URL` |
| CONTENT — captions | `LegacyOdysseusCaptionAdapter` → `app/caption_ai.py` | A thin `POST ctx → {caption, model}`; **"Odysseus owns model routing"** — Mise hands context and takes back one caption | `MISE_ODYSSEUS_CAPTION_URL` (+ bearer) |
| CONTENT — packs | `LegacyDionysusPackAdapter` → `app/platekit.py` | Dionysus/Platekit is a **separate content-CRM product** with its own per-org "packs" and DB; Mise only *reads* approved packs and *triggers* a draft after Argus completes — not a model wrapper | `MISE_PLATEKIT_API_BASE` (+ bearer) |
| PRODUCTS | `ProductsRenderAdapter` | Dormant scaffold — **no outbound call is ever made**, `serves_production=False`, deterministic budget/consent guards in `app/products.py` | (would-be `MISE_PRODUCTS_RENDER_URL`) |
| OFFERS / ALBUMS | — | **Decommissioned** in migration `075` (Plutus/Mnemosyne) | — |

None of these sidecars is co-deployed (`docker-compose.yml` does not include
them); the vision/album challenger endpoints are documented homelab hosts on a
tailnet mesh.

**Architecture directive (Kevin).** AI capability must live inside the app's own
deployable unit. Calling an external **hosted** API directly (a model vendor,
the way Mise already calls Stripe) is acceptable — a normal vendor dependency.
Depending on a **separate self-hosted service**, and especially on the operator's
personal homelab, is **not acceptable** for a hosted product holding other
people's data.

The existing consolidation roadmap already moves sidecar → internal via a
strangler, and its *machinery* is exactly what we want (a provider registry seam
in `app/providers/registry.py`, the `ai_runs` provenance ledger, a shadow-compare
engine, one-env-var rollback to the legacy adapter, per-capability decommission
gates). But its stated **target** — "1 app + N stateless workers" — still permits
*separate* workers, and one of its own "internal" implementations (the Qwen3-VL
challenger) is a homelab service. That target must be re-pinned to the directive.

## Decision

**Every AI capability resolves to exactly one of two acceptable topologies, both
inside Mise's deployable unit:**

- **(A) In-process Python** in Mise — deterministic transforms, orchestration,
  file access, DB writeback — with no network hop to a self-hosted peer.
- **(B) A direct call to an external hosted model API** — a vendor dependency
  (Anthropic / OpenAI / xAI / Google / a hosted render vendor), invoked from
  in-process Mise code and keyed by a vendor API key in env.

**Explicitly excluded:** (C) a dependency on a separate self-hosted service
(the Argus / Odysseus / Dionysus siblings) or the operator's homelab
(`mickeybot`, `strix-halo-*`) for any production/hosted path. A self-hosted model
may remain a **local dev / eval tool** but is never eligible for
`serves_production` and never on a tenant's request path.

**All existing strangler mechanisms are preserved unchanged:** `registry.resolve`
stays default-legacy and flips per capability by feature flag; `ai_runs` records
`ProviderResult.provenance()` for every call; shadow mode records both sides to the
ledger and mutates nothing; a cutover flag defaults to and rolls back to the legacy
adapter with one env var; §11.4 holds — adapters return `HUMAN_REVIEW` and never
write authoritative state, only the deterministic caller writes on an `OK` result.

### Per-capability target and sequence

**1. CONTENT / captions — first (simplest, lowest risk).**
- *Now:* `POST` to Odysseus, which routes the model opaquely.
- *Target:* **(B)** a new in-process `InternalCaptionAdapter` that builds the same
  `ctx`→prompt inside Mise and calls a hosted text API directly. Odysseus's hidden
  "model routing" becomes an explicit Mise config value (e.g. `MISE_CAPTION_MODEL`).
- *Seam / flag:* reuse `Capability.CONTENT` and the already-wired
  `MISE_PROVIDER_FACADE_CONTENT` flag (roadmap Phase 1). Shadow the internal draft
  against the legacy Odysseus draft; cut over one drafting surface at a time.
- *Rollback:* flag off → legacy `caption_ai` adapter (byte-identical path).
- *Decommission Odysseus caption endpoint:* after output parity on a fixed
  drafting set + rollback rehearsal. Safe to lead with: captions are already an A1
  reversible draft, non-mutating on failure.

**2. VISION — second (heaviest; owns file access + writeback + new cost).**
- *Now:* Argus reads originals via the shared media root, calls the vision model,
  owns a run store, and calls back → `argus_writeback` writes structured fields.
- *Target:* **(B)** a new in-process `InternalVisionAdapter` that reads **Mise's own
  media** (Mise already has the files — no shared-root coupling needed), calls a
  hosted vision API directly, normalizes to the existing structured schema
  (`schemas/*.schema.json`), and writes through the **existing `argus_writeback`
  path**. Batching/dedup move onto Mise's own `jobs` queue.
- *Seam / flag:* reuse the `active_vision_provider` interlock + `MISE_VISION_PROVIDER`
  switch already built in `registry.py` (it already refuses to route production onto
  an `serves_production=False` provider). Register the hosted-API adapter as a new
  production-eligible provider name; **retire Qwen3-VL/`mickeybot` from the
  production consideration set** (homelab is not a hosted path — keep only as a
  never-`serves_production` local eval tool, or drop).
- *Shadow → validate → cut over:* the shadow machinery already exists and is
  ledger-only; validate on a **consent-cleared** image set (audit §9.5), then switch
  one gallery type.
- *Rollback:* `MISE_VISION_PROVIDER=argus`; assets are re-writeable from a legacy run.
- *New cost surface:* hosted vision is priced per image. The `ai_runs.cost_usd`
  ledger becomes load-bearing, and vision should gain an Aphrodite-style spend cap
  before cutover. This is the accepted trade — a metered vendor dependency in
  exchange for removing the self-hosted peer.
- *Decommission Argus service:* unchanged roadmap Phase 2 gate — parity on the
  validation set **+** 30-day cost ledger **+** run-store restore test **+**
  observation period. Argus stays the default worker until then.

**3. CONTENT / packs (Dionysus) — reframe, then decide (defer).**
- Dionysus/Platekit is a **separate content-CRM product**, not a model call, so
  "swap for a hosted API" does not describe it. Two sub-questions for a follow-up
  Opus decision (do not assume): **(i)** if per-client "packs" stay a product
  feature, only the pack *drafting* step is an AI call that can go **(B)** in-process;
  the pack *storage / CRM* is a product-scope question, not an AI-topology one.
  **(ii)** if packs are not core to the F&B commercial workflow — as Plutus and
  Mnemosyne were judged not to be (migration 075) — then **Dionysus is a
  decommission candidate**, not a consolidation target. This is the lowest-urgency
  path: it is an operator-convenience read/trigger, off the client-delivery
  critical path. Flag for Kevin.

**4. PRODUCTS (Aphrodite) — no change now; same rule when armed.**
- Stays dormant with its deterministic budget/consent guards. When armed it must
  follow the same rule: **(A)** in-process render orchestration + **(B)** a hosted
  render vendor — never a self-hosted render sibling. (ADR 0021 unchanged.)

## Consequences

- The deploy surface collapses from *Mise + Argus + Odysseus + Dionysus* toward
  **one deployable unit + hosted-vendor API keys**. The homelab (`mickeybot`,
  `strix-halo-*`) leaves the production path entirely.
- **New dependency shape:** hosted model APIs are metered and are an availability
  dependency. This is deliberately accepted — the same class of dependency as
  Stripe — in exchange for eliminating the self-hosted-peer coupling the directive
  forbids. `ai_runs` (cost/latency) and a per-capability spend cap become
  load-bearing where they were optional.
- **Secrets change shape**, feeding O4 (credential hygiene): from several per-sidecar
  bearer tokens (rotation drift; the Plutus 401, audit §3.2) to a small set of
  hosted-vendor API keys — fewer secrets, but each now production-critical and
  client-media-adjacent. Transport is now vendor TLS, not plain-`http` LAN.
- **Config deprecation:** `MISE_ARGUS_*`, `MISE_ODYSSEUS_*`, `MISE_PLATEKIT_*`, and
  `MISE_VISION_CHALLENGER_URL` each become deprecated **at their own decommission
  gate** — removed one capability at a time after parity + observation, never in a
  flag-day.
- **§11.4 preserved throughout.** Every adapter still returns `HUMAN_REVIEW` and
  never writes authoritative state; only the deterministic caller writes on `OK`, so
  a flag flip mid-flight cannot leave a partial write.
- CLAUDE.md's capability list and `.env.example`'s dead sidecar blocks are corrected
  in the same change set as this ADR.

## Alternatives considered

- **Keep the sidecars but co-deploy them in one compose stack.** Rejected: they
  remain separate services with separate state authorities and backup chains; that is
  not "inside the app's deployable unit," and the homelab challenger dependency
  survives.
- **Self-host the models *inside* Mise's unit (bundle Ollama/GPU).** Rejected: pins
  heavy GPU/infra onto every deploy of a hosted product; the directive explicitly
  blesses hosted-API vendor calls as the lighter acceptable path, and a homelab GPU
  is exactly what we're removing.
- **Flag-day rewrite of all capabilities at once.** Rejected: violates the strangler
  invariant. The whole design is one-capability-at-a-time behind a flag with rollback
  to the legacy adapter and a decommission gate.

## Implementation note (not this ADR)

No code ships with this ADR. The first implementation slice — an in-process caption
adapter behind `MISE_PROVIDER_FACADE_CONTENT`, in shadow mode, non-mutating — is a
separate **red-light draft PR** (it touches the provider contract path), human-merged
per CLAUDE.md. Vision and the Dionysus decision follow as their own slices and ADRs.
