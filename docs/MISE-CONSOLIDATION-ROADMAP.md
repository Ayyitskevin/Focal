# Mise Consolidation Roadmap

> Ordered, strangler-style migration from "Mise + a fleet of sidecars" to "Mise Solo
> Studio OS + replaceable workers." Each phase is a set of **vertical slices** with
> explicit acceptance criteria, a shadow-mode plan, and a rollback. **Never a flag-day
> replacement.**

See [`MISE-SOLO-STUDIO-OS.md`](MISE-SOLO-STUDIO-OS.md) for the target and
[`REPO-CONSOLIDATION-MATRIX.md`](REPO-CONSOLIDATION-MATRIX.md) for per-repo detail.

## The strangler contract (applies to every capability)

1. Establish a stable internal Mise domain interface.  ← **Phase 0 (done, this branch)**
2. Wrap the existing external service as the **legacy adapter**.  ← **Phase 0 (done)**
3. Add an internal implementation behind a **feature flag**.
4. Run internal and legacy paths in **shadow mode** where safe.
5. Compare schema validity, quality, latency, cost, operator acceptance.
6. Switch **one workflow at a time**.
7. Preserve **rollback to the legacy adapter**.
8. Decommission the sibling **only after** parity + restore testing + an observation
   period.

The Phase 0 `providers` facade implements steps 1–2 and the *mechanism* for 3–7
(`registry.resolve` defaults to legacy; `registry.use()` injects an alternate adapter;
`ProviderResult` is the comparable shadow record). Steps 3–8 are the later slices below.

---

## Phase 0 — Foundation (this branch) ✅

**Goal:** one typed internal contract for photography AI; legacy calls wrapped as
adapters; mocks; tests; **zero production behavior / route / env / schema change.**

| Slice | Deliverable | Status |
| --- | --- | --- |
| 0.1 | `app/providers/contracts.py` — `Capability`, `ProviderResult`, `ResultStatus`, `ReviewRequirement`, `provenance()` | ✅ |
| 0.2 | `app/providers/adapters.py` — legacy adapters for Argus / Plutus / Odysseus-caption / Dionysus-packs (wrap **non-mutating** trigger/draft funcs) | ✅ |
| 0.3 | `app/providers/mocks.py` — deterministic mocks + `FailingAdapter` | ✅ |
| 0.4 | `app/providers/registry.py` — default-legacy resolution + `use()`/`reset()` seam | ✅ |
| 0.5 | `tests/test_providers.py` — 25 unit tests (contract, mapping, **non-mutating-on-failure**, disabled, mocks, registry) | ✅ |
| 0.6 | Analysis docs + ADRs | ✅ |

**Acceptance (met):** unit gate green (61 passed); ruff clean; **no new smoke failures**;
nothing in the running app imports `app/providers/`; no migration.
**Rollback:** delete the additive package + test + docs — there is no behavior to revert.

---

## Phase 1 — Provenance + provider seam in production (no authority change)

**Goal:** make the `providers` facade *real* in production for the lowest-risk path,
and persist unified provenance — **the first red-light migration**. **Implemented as a
draft PR** (`migrations/065_ai_runs.sql`, `app/ai_runs.py`, the
`MISE_PROVIDER_FACADE_CONTENT` flag, and the `admin/recurring.py` wiring); human-merged
because it adds a migration.

| Slice | Deliverable | Acceptance | Rollback | Status |
| --- | --- | --- | --- | --- |
| 1.1 | **`ai_runs` provenance table** (`migrations/065_ai_runs.sql`) storing `ProviderResult.provenance()` per call: capability, provider, model, status, review, latency_ms, cost_usd, tokens, correlation/idempotency key, subject ref. `app/ai_runs.record()` writes it (bound params). | Forward-only, additive (new table + 2 indexes); no existing table touched; `rollback/065_ai_runs.sql` drops it. | Stop writing `ai_runs`; run the rollback. | ✅ in PR |
| 1.2 | Route **caption drafting** (`admin/recurring.py`) through `providers.resolve(Capability.CONTENT)` when the flag is on, recording provenance. Lowest risk (already A1 draft, already non-mutating). | Flag OFF (default) = byte-identical legacy `caption_ai` path, no `ai_runs` row; flag ON = facade + provenance row; `CaptionDraftError`/DISABLED stays non-mutating. | Flip `MISE_PROVIDER_FACADE_CONTENT` off → direct `caption_ai` call. | ✅ in PR |
| 1.3 | Content-facade flag in `app/features.py` (`content_provider_facade_enabled()`), defaulting legacy/off; documented in `.env.example`. | Disabled by default; flag documented. | Remove flag; default already legacy. | ✅ in PR |

**Shadow mode:** N/A (still one provider per capability). **Data migration:** 1.1 only —
red-light, human-merged. **Why first:** caption path is already a reversible draft with
no money/publication consequence.

---

## Phase 2 — Argus vision behind the seam + internal challenger (shadow)

**Goal:** vision as the common upstream signal; prove a challenger before switching.

The **shadow machinery shipped first, asset-safe and inert** (a draft PR): the
comparison engine, the challenger seam, and a runner that records both sides to the
ledger without ever touching the live publish/analyze path. The cost-incurring pieces (a
real challenger backend + the auto-trigger) are deliberately separated so no live vision
flow changes until a challenger is deliberately wired.

| Slice | Deliverable | Acceptance | Rollback | Status |
| --- | --- | --- | --- | --- |
| 2.2-seam | **Vision challenger seam** — `providers.registry.challenger()`/`use_challenger()` (default None), `MockVisionChallengerAdapter`. | Conforms to `Capability.VISION`; default empty → shadow inert. | Remove challenger registration. | ✅ in PR |
| 2.3-engine | **Shadow runner + comparison** — `app/providers/shadow.compare()` (pure) and `app/vision_shadow.run_for_gallery()`: snapshots the *completed* legacy run (no re-call), runs the challenger, records BOTH to `ai_runs` (linked by correlation id), compares. **Never writes assets/galleries; never raises; no-op unless `MISE_VISION_SHADOW` is armed AND a challenger is registered.** Job handler `vision_shadow_gallery`. | Two linked `ai_runs` rows; zero asset/gallery mutation; inert by default. | Flag off / drop challenger; ledger-only so nothing to revert. | ✅ in PR |
| 2.2-backend | **Real internal vision adapter** (direct cloud vision or a local worker) registered as the challenger. | Conforms to contract; mock-tested first; off by default. | Drop challenger registration. | ⏳ next (may be red-light if it needs new provider config/keys) |
| 2.1 | Auto-trigger: `argus_analyze._enqueue_shadow()` enqueues `vision_shadow_gallery` after a completed Argus run, behind the flag (mirrors `_enqueue_writeback`). | Publish/analyze path unchanged when off; shadow fires only when armed (and the job no-ops without a challenger). | Flag off. | ✅ in PR |
| 2.4 | Switch one gallery type to challenger **only if** it matches/beats the validation set. | Human-scored parity on 100-image set; per-image cost/latency logged. | Flag → legacy Argus; assets re-writeable from legacy run. | ⏳ later |

**Decommission gate (Argus service):** parity on validation set **+** 30-day cost ledger
**+** restore test of the run store **+** observation period. Until then Argus stays the
**default worker** and the legacy adapter is never removed.

---

## Phase 3 — Plutus offers, idempotent client links

| Slice | Deliverable | Acceptance | Rollback |
| --- | --- | --- | --- |
| 3.1 | Route recommend through `providers` (`Capability.OFFERS`); legacy default. | One idempotent offer per gallery preserved; `plutus_last_*` + `ai_runs` written; retry never duplicates. | Flag → legacy. |
| 3.2 | Operator review/edit UI for offers in Mise (offers are **A1 drafts**). | No auto-send, no auto-charge; human approves each offer/pitch. | Hide UI; offers remain proposal-only. |
| 3.3 | **30–60 day offer-acceptance/revenue scorecard.** | Measured revenue lift or it is a retire candidate (audit §19.4). | — |

**Money guardrail:** any pricing/SKU rule that affects an invoice is **red-light** —
proposal only, deterministic Mise code + human applies it. Never let the model set price
or settlement.

---

## Phase 4 — Dionysus content as a common drafting module

| Slice | Deliverable | Acceptance | Rollback |
| --- | --- | --- | --- |
| 4.1 | Unify caption + pack drafting under `content_ai` via `providers` (`Capability.CONTENT`). | All drafts A1, human-accept; human-body guard preserved; prior content untouched on failure. | Flag → direct modules. |
| 4.2 | Rebuild Dionysus enrichment as an **Odysseus skill** behind the existing API; compare output. | Same-or-better output on a fixed task set; reversible cutover. | Keep Dionysus/Platekit API; flag → sidecar. |

**Decommission gate (Dionysus sidecar):** output parity on a fixed drafting set +
rollback rehearsal (audit §19.4).

---

## Phase 5 — Mnemosyne albums (shadow only)

| Slice | Deliverable | Acceptance | Rollback |
| --- | --- | --- | --- |
| 5.1 | `albums` module + **album-draft schema** (red-light migration, designed then PR'd). | Forward-only; additive. | Drop writes; additive tables. |
| 5.2 | `Capability.ALBUMS` adapter (worker proposes draft from vision signals). | Conforms to contract; mock-tested. | Flag off. |
| 5.3 | **Deterministic layout validator**: every selected asset placed exactly once, belongs to the gallery, none omitted/duplicated/misassigned. | Validator rejects any malformed layout before it reaches a human. | — |
| 5.4 | **Shadow pilot on 3 representative galleries**; human acceptance + time data. | ≥70% placements acceptable; representative album < 30 min (audit §5.1); operator time saved. | Manual album workflow; no production dependency. |

---

## Phase 6 — Aphrodite product images (optional, later)

Only if current Mise clients/workflows show near-term commercial value. `products`
module; render-worker contract; **spend guards**; **no automatic client publication**;
licensing/consent review (audit §13.5). Budget-capped, human-approved, export-gated.

---

## Phase 7 — Decommissioning

Retire redundant sidecar UI/auth/DB/queues/backups **per capability**, each only after
its decommission gate (parity + restore test + observation). See "Services that can
eventually be retired" in the final report. **Order of reversibility (audit §16.7):**
observe → add independent backups/monitoring → staging+shadow → control-plane adapter
without moving authority → move noncritical background work → change a contract behind a
flag → only then migrate authoritative data or retire a service.

---

## Shadow-mode strategy (general)

- Shadow writes go to `ai_runs` **only**; never to `assets`, `galleries`, offers,
  captions, or any client-facing/authoritative record.
- Compare per `ProviderResult`: status validity, output quality vs human reference,
  hero/keyword agreement, latency, cost, review disposition.
- A challenger is promoted only when it **matches or beats the metric that matters on a
  fixed, consent-cleared validation set** (audit §9.5) — not on benchmark scores.
- Keep output + cost ledgers by model version; a model upgrade is a production change
  with a regression suite (audit §9.5, §17.2).

## Rollback strategy (general)

- Every cutover is a **feature flag** that defaults to and can return to the legacy
  adapter; the legacy adapter is never deleted until the decommission gate passes.
- Authoritative state is only ever written by the **deterministic caller** on an `OK`
  result; a failed/disabled/invalid result mutates nothing, so a flag flip mid-flight
  cannot leave a partial write.
- Schema changes are forward-only and additive (new tables/columns); rollback = stop
  writing, never drop.

## Data-migration needs (all red-light, PR'd separately)

| Need | Phase | Shape |
| --- | --- | --- |
| `ai_runs` provenance table | 1.1 | new table; additive; forward-only |
| Album-draft tables | 5.1 | new tables; additive |
| Product-job tables | 6 | new tables; additive |
| Notion adapter: store `data_source_id`, upgrade API version | parallel track | red-light; contract-tested; staging+shadow first (audit §11.2) |

No schema change ships in Phase 0.

## Estimated operational reduction

**[CALCULATED ESTIMATE — validate with the audit's 30-day sidecar scorecard, §15.4.]**
Consolidation does not cut compute; it cuts **operational surface**:

- **Deploy/runtime surfaces:** from ~5 independently-deployed services (Mise + Argus +
  Plutus + Dionysus + workers) toward **1 app + N stateless workers** — fewer systemd
  units, one deploy story, one backup chain that already restore-tests nightly.
- **Auth surfaces:** from per-sidecar bearer tokens with drift risk (the Plutus 401,
  audit §3.2) toward one service-token register + the existing single admin auth.
- **State authorities:** from per-service SQLite "second authorities" toward **one
  business spine** + disposable worker caches.
- **Review surfaces:** from per-capability ad-hoc status columns toward one `ai_runs`
  provenance ledger + one review queue — now surfaced read-only to the operator at
  `/admin/ai-runs` (capability filter, status badges so failures are visible, CSV export).

Each retirement must show ≥30 days of "no time saved / no revenue / no risk reduced"
before a service is paused (audit §15.4, §19.4) — the reduction is *earned per gate*,
not assumed.
