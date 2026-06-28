# ADR 0026 — Decommission the Mnemosyne ALBUMS and Plutus OFFERS subsystems

**Status:** Accepted (supersedes ADRs 0009, 0011, 0012, 0018, 0019, 0020, 0022, 0023)
**Date:** 2026-06-28
**Deciders:** Kevin (owner), principal engineer

## Context

Mise's AI sidecars grew from a generic "photography studio" frame. Two of them —
**Plutus / OFFERS** (print & album upsell bundles) and **Mnemosyne / ALBUMS** (lay-flat
album-spread layout + record-only print orders) — are **consumer/portrait-shaped**: they assume
a client who buys prints and coffee-table albums.

The operator's actual business is **solo, commercial, food-and-beverage**. Clients are
**companies** — restaurants, brands, agencies — who receive **licensed digital files**, not
print products or wedding albums. After a market + codebase review (subscription/pricing fatigue
is the dominant churn driver; niche-fit beats breadth), the owner's decision was to **stop
carrying capabilities that don't fit the niche** and reinvest in the commercial spine (B2B
invoicing, retainers, licensing — see ADR 0025).

Both subsystems were dormant in production: Mnemosyne never had an armed proposer (the baseline
was deterministic-only), and Plutus offers required an external endpoint that was never set. So
removal carries no live-data or live-flow risk.

## Decision

**Remove both subsystems entirely** — code, admin surfaces, provider-facade capabilities, AI-pane
tiles, the offer→invoice and album→invoice bridges, config, nav, schemas, and tests — and drop
the orphaned schema (migration 075).

- **Provider facade.** `Capability.OFFERS` and `Capability.ALBUMS` are removed from the contract;
  their adapters (`LegacyPlutusOffersAdapter`, `InternalAlbumBaselineAdapter`,
  `InternalAlbumChallengerAdapter`), mocks, registry entries, and the album-adopt seam
  (`active_album_provider` / `album_proposer_adapter`) are deleted. VISION, CONTENT, and PRODUCTS
  (dormant) are untouched.
- **Vision path preserved.** The Argus → (Qwen cutover) vision pipeline stays exactly as is; only
  the post-analysis `plutus_recommend` enqueue hop is removed. `validation_set` and all
  shadow/cutover machinery are vision-only and remain.
- **Money path preserved.** Core invoicing/proposals/contracts/payments are untouched; only the
  two one-click upsell bridges (`from-offer`, `from-album`, `add-offer-items`) and the offer
  tiles on the money/AI ops panes are removed.
- **Schema (migration 075).** Drops `album_drafts` + `album_placements` and the 13 `plutus_*`
  columns on `galleries`. The creating migrations (055/059/062/066/068/069/070/072) are **kept**
  (never edit/delete a merged migration); 075 drops on top — correct for both fresh and existing
  DBs. A rollback recreates the schema (structure only; the data was dormant).

## Consequences

- **Positive:** the surface area now matches the business. ~25 files deleted, the AI/money panes
  read clean, and the operator no longer sees wedding/portrait upsell UI that never applied.
- **Red-light change:** drops tables + columns and removes provider-contract members + a
  money-path bridge. Ships as a reviewed draft PR a human merges (not self-applied).
- **Reversible by structure:** migration 075 has a rollback; the deleted code lives in git
  history. Re-introduction, if ever wanted, is a revert + re-arm — but the deliberate stance is
  that these don't belong in this product.
- **Superseded ADRs** (0009, 0011, 0012, 0018, 0019, 0020, 0022, 0023) are kept as history with a
  superseded banner pointing here; the planning docs (consolidation roadmap/matrix, sibling
  briefs MNEMOSYNE/PLUTUS) are point-in-time records and are annotated, not rewritten.

## Alternatives considered

- **Leave them dormant.** Rejected — dormant-but-present still costs: nav clutter, AI-pane tiles,
  test weight, and the standing temptation to "just arm it." The owner chose a clean cut.
- **Keep the tables, remove only the UI/code.** Rejected — a half-cut leaves orphaned columns on
  the core `galleries` table and a misleading schema; the drop migration is the honest end state.
- **Cut albums only, keep Plutus offers.** Rejected — print/album *upsell bundles* are the same
  consumer-shaped assumption as album spreads; both miss the B2B commercial workflow.
