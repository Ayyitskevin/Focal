# Sibling Improvement Plan — making each repo a better OS worker

> The prioritized, executable backlog for improving the sibling repos so they serve Mise Solo
> Studio OS. Each item conforms to the [Worker Contract](WORKER-CONTRACT.md) and a
> [schema](../schemas/). Ordered by **leverage to the OS**, not by repo.
>
> **Status of execution:** these are work-orders. They are authored in Mise (the OS hub)
> because the sibling repos are separate; each is executed *in its own repo* once that repo is
> in scope (the Mise side of every contract is already built and cited below).

Ranking rule (audit §3.3, §19.3): the best improvement turns an engine into a **stateless,
contract-true worker** Mise can drive, compare, meter, and retire.

> **Ready-to-paste prompts:** the executable per-repo prompt sequences (umbrella + discrete
> steps) live in [`sibling-briefs/`](sibling-briefs/README.md) — one file per repo, copy a
> block into that repo's session.

---

## P0 — Shared worker contract + schemas  ✅ (this repo)

The cross-cutting prerequisite, and the one piece buildable inside Mise:
[`WORKER-CONTRACT.md`](WORKER-CONTRACT.md) + [`../schemas/`](../schemas/) (vision, offers,
albums, products). Every item below conforms to these. **Done** — the rest are downstream.

Recommended next: a small **shared worker SDK** (one library implementing the contract once)
so P1–P5 adopt it instead of re-deriving idempotency/callbacks/provenance/validation/health.

## P1 — Argus (vision): emit the structured vision schema + cost  → unblocks the cutover

- **Change:** add a structured-JSON mode that returns `vision.schema.json`
  (`{photos:[{basename,keywords,alt_text,keeper_score,hero_potential}]}`) and report
  `cost_usd` + `latency_ms` in the callback body.
- **Why:** the vision cutover is blocked only because Argus and Qwen3-VL don't emit the *same*
  structured signals, so the validation gate can't compare like-with-like. This makes
  shadow → validation → cutover apples-to-apples and lets the dry-run preview
  (`/admin/vision-cutover`) tune against a real contract.
- **Mise side already built:** `app/qwen_writeback.parse_structured`, `app/argus_writeback`
  (`assets.argus_*`), shadow/validation/cutover seam (ADR 0016/0017).
- **Acceptance:** Argus + Qwen outputs both validate against `vision.schema.json`; paired
  shadow rows differ only by provider; cost shows in the AI cost report.

## P1b — Qwen host (mickeybot): a thin structured-output adapter

- **Change:** wrap the Qwen3-VL endpoint so it returns `vision.schema.json` directly (cost 0,
  local), removing the prompt-tuning gymnastics.
- **Why:** the literal blocker for Track A activation — the moment this exists, the cutover is
  a flag flip. (Not a "repo" so much as the endpoint contract.)

## P2 — Plutus (offers): SKU/line-item linkage + auth fix  → makes the scorecard real

- **Change:** emit `offers.schema.json` with stable `sku` per bundle and optional `line_items`
  that map 1:1 to invoice lines; fix the bearer-token drift (the audit's 401).
- **Why:** the offer scorecard (ADR 0020) can only show a *project-level proxy* for revenue
  because there's no offer→sale link. SKU linkage upgrades it to **real attributed upsell
  revenue** — the evidence the retire-gate (audit §19.4) needs.
- **Mise side already built:** offers queue + send + scorecard; `plutus_last_*`, `emails_log`.
- **Acceptance:** an accepted offer's SKU appears on the resulting invoice line; the scorecard
  reports attributed revenue, not just the proxy.

## P3 — Odysseus: become the single model gateway

- **Change:** make Odysseus the one "propose, never mutate" gateway for all model calls
  (vision/content/albums), emitting `ProviderResult` + provenance and enforcing the structured
  schemas. The other engines become skills behind it.
- **Why:** highest architectural leverage — one well-behaved provider, one auth, one cost
  ledger; it's what lets Dionysus collapse (P4) and every future capability plug in cleanly.
- **Acceptance:** Mise's content/vision/album adapters talk to one gateway; cost/latency land
  in `ai_runs` uniformly.

## P4 — Dionysus (content): collapse into an Odysseus skill

- **Change:** rebuild pack/caption generation as a skill behind the Odysseus gateway,
  conforming to the content `ProviderResult`; prove output parity on a fixed drafting set.
- **Why:** one content provider, one deploy; enables retiring the Dionysus sidecar (matrix
  decommission gate).
- **Mise side already built:** `app/platekit.py`, `app/caption_ai.py`, content facade flag.
- **Acceptance:** parity on the fixed set + rollback rehearsal; flag cutover, then retire.

## P5 — Capability backends for the dormant foundations

- **Mnemosyne (albums):** emit `albums.schema.json` layouts that pass `validate_core`
  (every placement an eligible photo, placed once, no slot collision), reading Mise's
  hero/keeper signals instead of duplicating media. Turns the album proposer from baseline to
  real (ADR 0009/0011).
- **Aphrodite (products):** a stateless render worker emitting `products.schema.json` with
  **actual `cost_usd`**, so the budget cap (ADR 0021) enforces real numbers. Arming it also
  needs the operator's budget number + consent/licensing policy.

---

## Explicitly NOT invested (audit)

| Repo | Decision | Why |
| --- | --- | --- |
| **Hestia** | Doctrine only — keep current, no runtime work | Guidance shapes the ADRs; a Hestia runtime/DB dependency is excluded by design. |
| **Athena** | Do not integrate | Second source of truth; reconsider only after Notion modernization shows a measured ≥25% lift (audit §19.2 — not earned). |
| **Midas** | Do not integrate | Unrelated market-terminal R&D; out of the photography SLO. |

---

## Order of reversibility (audit §16.7)

Every per-repo change follows the strangler order: observe → add independent
backups/monitoring → staging + shadow → control-plane adapter without moving authority →
move noncritical background work → change a contract behind a flag → **only then** migrate
authoritative data or retire a service. No flag-day replacement; the legacy adapter stays and
rollback is a flag until the decommission gate (parity + restore test + observation) passes.
