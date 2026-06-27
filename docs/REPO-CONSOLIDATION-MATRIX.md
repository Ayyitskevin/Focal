# Repository Consolidation Matrix

> What to extract from each sibling repository, where it lands in Mise, what to discard,
> and how confident we are. **We do not merge Git histories or copy whole apps** — we
> rebuild/extract only the domain capability that belongs inside Mise, conforming to
> Mise's conventions and shared infrastructure.

**Evidence labels:** see [`MISE-SOLO-STUDIO-OS.md`](MISE-SOLO-STUDIO-OS.md#evidence-labels).
**Important scope note:** this session's workspace contains **only `mise`**; the sibling
repos are not checked out here. Therefore:

- Claims about **Mise's own integration code** are **[CODE]** — verified by reading the
  files named below.
- Claims about **sibling-repo internals** are **[DOC]/[INFER]** — sourced from the
  2026-06-25 Technical Audit (repo decision matrix §3.2, source register §21) and from
  the *Mise-side contract* each integration implements. Exact sibling file/function
  names should be confirmed against each repo before extraction; they are cited from the
  audit's source register where given.

Confidence column reflects the **decision** (keep/extract/discard), which is high even
where a specific sibling filename is INFER, because Mise's own integration code already
pins the contract that matters for consolidation.

---

## Summary

| Repo | Keep? | Target Mise module | First action | Consolidation | Conf. |
| --- | --- | --- | --- | --- | --- |
| **argus** | Yes, bounded | `vision` (behind `providers`) | wrap behind Phase 0 facade | share substrate; keep cloud-vision adapter | High |
| **plutus** | Only with ROI | `offers` (behind `providers`) | wrap; measure offer acceptance | consolidate plumbing; retire if no lift | High |
| **dionysus** | Capability yes | `content_ai` (behind `providers`) | wrap; rebuild as Odysseus skill | consolidate; retire sidecar after parity | High |
| **mnemosyne** | Capability, shadow | `albums` (new) | shadow pilot only | share analysis + job ledger | Medium |
| **aphrodite** | Later/optional | `products` (new) | isolate, budget-cap | share worker/audit substrate | High (defer) |
| **hestia** | Guidance only | — (no runtime dep) | extract design lessons | **none** | High |
| **athena** | No | — | do not integrate | **none** | High |
| **Midas** | No | — | do not integrate | **none** | High |

---

## Argus — vision

| Field | Detail |
| --- | --- |
| **Capability to keep** | Vision-analysis jobs; keywords / alt text / IPTC / captions; technical + editorial culling and hero signals; per-shoot review/writeback; cost/latency/model/prompt provenance. **[DOC]** (audit §3.2) |
| **Exact source — Mise side (authoritative contract)** | `app/argus_analyze.py` (`is_enabled`, `trigger_gallery_analyze`, `run_for_gallery`, `apply_callback`, `media_count_changed`); `app/argus_writeback.py` (`fetch_run_export`, `apply_to_gallery` → `assets.argus_*`, `galleries.argus_hero_asset_ids`); inbound `app/service_api.py` `GET /api/galleries`, `POST /api/argus/callback`. **[CODE]** |
| **Exact source — Argus repo** | `docs/DOGFOOD-STANDARD.md` (June 23 2026 Grok-only vision standard, CI mock-only) `[R-ARGUS-01]`; `README.md` (Lightroom/Capture One hooks) `[R-ARGUS-02]`. **[DOC]** |
| **Tests / invariants to preserve** | Publish enqueues exactly one analyze job & is idempotent; disabled → no job, no spurious error state; queued/sync recording; **errors swallowed, status recorded, no crash**; writeback matches photos by basename, hero pick = top-N by `hero_potential ≥ 0.5`; **never auto-deletes — human selection final** (audit §10.4). Mirror of existing `tests/test_smoke_argus.py`, `tests/test_argus_writeback.py`. **[CODE]** |
| **Infrastructure to discard on cutover** | Argus's own deploy/systemd, its SQLite for run state *as a second authority*, duplicated auth, any separate UI once review moves into Mise. Keep Argus as a **stateless worker** (run cache + reproducible outputs only). **[DOC]** |
| **Target module** | `vision`, behind the `providers` facade (`Capability.VISION`). |
| **Migration dependency** | Phase 0 facade (done). Then internal-vision adapter + shadow compare against the Grok path. Argus stays the legacy adapter and default until a local/cloud challenger beats it on the validation set (audit §9.5). |
| **Risk** | Cloud image privacy, per-image cost, token scope; basename-matching mismatches on re-ingest. |
| **Confidence** | **High** (Mise contract is [CODE]). |

## Plutus — print/album offers

| Field | Detail |
| --- | --- |
| **Capability to keep** | Print/album bundle recommendation; SKU & pricing-rule application; photo-to-product mapping; operator review/edit; pitch drafting; **one stable, idempotent offer per gallery**. **[DOC]** (audit §3.2) |
| **Exact source — Mise side** | `app/plutus_recommend.py` (`is_enabled`, `trigger_gallery_recommend`, `run_for_gallery`, `apply_callback`, bundle-meta parse); recording into `galleries.plutus_last_*` (`migrations/055_plutus_upsell.sql`, `059_plutus_offer_url.sql`); inbound `POST /api/plutus/callback`; chained after Argus completes (`argus_analyze.apply_callback` / `run_for_gallery`). **[CODE]** |
| **Exact source — Plutus repo** | `README.md` + recent commits (studio upsell, narrowed scope; recent auth drift → 401) `[R-PLUTUS-01]`. **[DOC]** |
| **Tests / invariants to preserve** | Recommend fires only after Argus / delivery event; **idempotent — one offer per gallery, retry does not duplicate**; disabled → dormant; failure recorded, never crashes; bundle-count/estimated-total parsing. Mirror of `tests/test_smoke_plutus.py`. **[CODE]** |
| **Infrastructure to discard** | **Do not reproduce** Plutus signup, tenant, subscription, Stripe, or a separate storefront — Mise already owns clients, galleries, invoices, payment state, operator identity. Discard duplicated auth/deploy/queue. **[DOC]** (audit §3.2: sales engine overlaps Hestia) |
| **Target module** | `offers`, behind `providers` (`Capability.OFFERS`). |
| **Migration dependency** | Phase 0 facade (done) + a **30–60 day offer-acceptance/revenue scorecard** (audit §19.4). Pricing/SKU rules that touch money are **red-light** — proposal only, human-applied. |
| **Risk** | Money-adjacent (offers → invoices); must never auto-charge or auto-send. Retire if no measured revenue lift. |
| **Confidence** | **High.** |

## Dionysus — campaign / caption / pitch content

| Field | Detail |
| --- | --- |
| **Capability to keep** | Campaign packs; gallery descriptions; captions & alt text; print-pitch enrichment; blog/email/social drafts; shot-list & campaign suggestions. **[DOC]** (audit §3.2) |
| **Exact source — Mise side** | `app/platekit.py` (`is_enabled`, `packs_for_client`, `notify_argus_complete` → `galleries.platekit_last_*`); `app/caption_ai.py` (Odysseus caption draft); config bridge `MISE_PLATEKIT_API_BASE`/`_TOKEN` with legacy `MISE_DIONYSUS_*` fallback; UI in `app/admin/studio.py`, `app/admin/content.py`, `app/admin/recurring.py`. **[CODE]** |
| **Exact source — Dionysus repo** | `README.md` (studio-mode content/pitch enrichment) `[R-DIONYSUS-01]`. **[DOC]** |
| **Tests / invariants to preserve** | All drafts are **A1 reversible drafts requiring human accept** — no auto-publish; failure leaves prior content untouched (`caption_ai` raises, caller writes nothing); human-body guard (`admin/recurring.py` `_is_human_body`) prevents overwriting edited captions. **[CODE]** |
| **Infrastructure to discard** | Separate user/billing/workspace/queue layer; duplicated auth/deploy. Rebuild capability as a **common content-drafting module + Odysseus skill** behind the existing API. **[DOC]** (audit §16.4, §19.4) |
| **Target module** | `content_ai`, behind `providers` (`Capability.CONTENT`). |
| **Migration dependency** | Phase 0 facade (done). Maintain the Dionysus/Platekit API until parity proven; retire only after parity + rollback rehearsal (audit §19.4). |
| **Risk** | Hallucination / rights misuse in client-facing copy — mitigated by draft-only + human review. |
| **Confidence** | **High.** |

## Mnemosyne — album planning & layout

| Field | Detail |
| --- | --- |
| **Capability to keep** | Album draft generation; story sequencing; spread grouping; hero selection; **layout validation**; operator correction/approval; export **only after review**. **[DOC]** (audit §3.2) |
| **Exact source — Mise side** | **None yet** — not integrated in Mise today. Would reuse `assets.argus_hero_potential` / `argus_keeper_score` as upstream signals and the `jobs` queue. **[INFER]** |
| **Exact source — Mnemosyne repo** | `README`/docs (album-design role, evolving integrations) `[R-MNEMOSYNE-01]`. **[DOC]** |
| **Tests / invariants to preserve** | **The model proposes; deterministic code validates.** Never silently omit, duplicate, or misassign images — a layout validator must assert every selected asset is placed exactly once and belongs to the gallery. Human layout review before export. **[DOC]** (audit §10.1 stage 16) |
| **Infrastructure to discard** | Its own media duplication, client links, separate run store as a second authority. Share Mise's analysis signals + job ledger. **[DOC]** |
| **Target module** | `albums` (new), behind `providers` (`Capability.ALBUMS`, roadmap). |
| **Migration dependency** | Vision capability live + an album-draft schema (new tables → **red-light migration**, roadmap only). **Shadow pilot on 3 real galleries** before any production dependency (audit §16.4, §19.4). |
| **Risk** | Image mis-assignment; media duplication; cloud COGS. |
| **Confidence** | **Medium** (no Mise code yet; capability proven elsewhere). |

## Aphrodite — product-image generation

| Field | Detail |
| --- | --- |
| **Capability to keep** | Source-asset intake; product-variant job planning; render-worker contracts; **spend guards**; human approval + export. **[DOC]** (audit §3.2) |
| **Exact source — Mise side** | **None** — intentionally out of the first consolidation phase. **[INFER]** |
| **Exact source — Aphrodite repo** | `README.md` (generation jobs, workers, reviews, cost/alert controls) `[R-APHRODITE-01]`. **[DOC]** |
| **Tests / invariants to preserve** | Budget-capped; **no automatic client publication**; explicit human approval before use; copyright/consent on generated imagery (audit §13.5). **[DOC]** |
| **Infrastructure to discard** | Treat as isolated worker sharing Mise's worker/audit substrate; no authoritative client/money state. |
| **Target module** | `products` (new), **optional/later**. Only if current Mise clients/workflows show near-term value. |
| **Migration dependency** | Everything above + commercial demand + licensing review. **Not phase 1.** |
| **Risk** | External image handling, cost runaway, generated-image copyright. |
| **Confidence** | **High** that it should be deferred. |

## Hestia — architecture guidance only

| Field | Detail |
| --- | --- |
| **Use** | Design lessons **only**: one product + one data spine; modules over microservices; provider seams; durable jobs; idempotency; human-reviewed AI; shared identity/storage/audit/billing foundations. `docs/HESTIA-DOCTRINE.md` `[R-HESTIA-01]`. **[DOC]** |
| **Explicitly excluded from Mise** | Multi-tenancy; public SaaS signup; studio-subscription billing; tenant-scoped duplication; **a Hestia runtime dependency**; **a Hestia DB migration**. |
| **Target module** | None — guidance is reflected in the ADRs, not in code. |
| **Confidence** | **High.** Keep independent; evaluate any future migration only through the audit §19.1 gates (currently **not passed**). |

## Athena & Midas — do not integrate

| Repo | Decision | Reason |
| --- | --- | --- |
| **athena** | **Do not integrate.** Notion remains the planning surface. | Self-hosted project/knowledge experiment; would create a second source of truth. Reassess only after Notion adapter modernization and a measured ≥25% operating improvement (audit §19.2 — **not earned**). **[DOC]** |
| **Midas** | **Do not integrate.** | Unrelated market-terminal R&D; exclude from the photography SLO. `[R-MIDAS-01]`. **[DOC]** |

---

## Cross-cutting: "consolidate the chassis, not the engines" (audit §3.3, §19.3)

Standardize the **shared substrate** across capabilities; do **not** merge codebases to
reduce repo count. Inside Mise this means every capability uses:

| Shared substrate | Mise home |
| --- | --- |
| AI result contract + provenance | `app/providers/contracts.py` (Phase 0) |
| Provider adapters + routing seam | `app/providers/adapters.py`, `registry.py` (Phase 0) |
| Durable jobs / retry | `app/jobs.py` |
| Audit trail | `app/audit.py` |
| Auth / service tokens | `app/security.py`, `app/admin/auth.py` |
| Storage / media namespace | `config.MEDIA_DIR/<gallery_id>/…` |
| Feature flags (dormant-by-env) | `app/features.py` |
| Health / ops evidence | `/healthz`, `app/ops_monitor.py` |

What stays **separate by design**: human auth (no new identity platform yet), payment
tables/webhook state (legal/financial blast radius), broad worker media access (least
privilege). (audit §19.3)

## Making each engine a better worker

The per-repo improvements that make a sibling more useful to the OS — and the order to do
them in — are specified as executable work-orders in
[`SIBLING-IMPROVEMENT-PLAN.md`](SIBLING-IMPROVEMENT-PLAN.md), all conforming to the
[`WORKER-CONTRACT.md`](WORKER-CONTRACT.md) and the machine-checkable
[`../schemas/`](../schemas/) (vision / offers / albums / products). The throughline: turn
each engine into a **stateless, contract-true worker** Mise can drive, compare, meter, and
retire — never merge codebases.
