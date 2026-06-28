# Architecture Decision Records — Mise Solo Studio OS

Decisions that shape the consolidation of the photography sidecars into Mise. Each ADR
states context, the decision, consequences, measured reopen criteria, and alternatives.

| ADR | Decision | Status |
| --- | --- | --- |
| [0001](0001-modular-monolith-plus-workers.md) | Modular monolith + optional stateless workers (Mise owns the job/review lifecycle) | Accepted |
| [0002](0002-mise-sole-transaction-authority.md) | Mise SQLite is the sole transaction authority | Accepted |
| [0003](0003-notion-bounded-mirror.md) | Notion is a bounded human mirror, never an authority | Accepted |
| [0004](0004-odysseus-provider-boundary.md) | Odysseus / model-provider boundary (propose, never mutate) | Accepted |
| [0005](0005-sqlite-retention.md) | Retain SQLite; no Postgres on spec | Accepted |
| [0006](0006-ai-provenance-and-human-approval.md) | One AI result contract; human approval; only OK writes | Accepted |
| [0007](0007-vision-challenger-qwen3-vl-local.md) | Vision challenger: Qwen3-VL on a local OpenAI-compatible endpoint | Accepted |
| [0008](0008-notion-api-modernization.md) | Notion API modernization — version-configurable + data-source create | Accepted |
| [0009](0009-album-layout-deterministic-validator.md) | Mnemosyne albums: a deterministic layout validator owns correctness | Superseded (0026) |
| [0010](0010-validation-scoring-promotion-gate.md) | Validation-scoring harness: a deterministic promotion gate | Accepted |
| [0011](0011-album-proposer-and-review-workflow.md) | Mnemosyne albums: deterministic baseline proposer + human review workflow | Superseded (0026) |
| [0012](0012-offer-approval-state.md) | Plutus offers: persisted operator approve/reject state | Superseded (0026) |
| [0013](0013-ai-operations-dashboard.md) | AI operations dashboard: one read-only pane over the consolidated capabilities | Accepted |
| [0014](0014-shadow-to-validation-bridge.md) | Shadow→validation bridge: enrol shadowed galleries into the gate from the ledger | Accepted |
| [0015](0015-ai-cost-report.md) | AI cost & activity report: COGS monitoring over the ledger | Accepted |
| [0016](0016-vision-cutover-seam.md) | Vision cutover seam: interlocked production-provider selection | Accepted |
| [0017](0017-qwen-production-writeback-scaffold.md) | Qwen vision production-writeback (dormant scaffold) | Accepted |
| [0018](0018-offer-send-money-path-boundary.md) | Offer send: deliver the link, never touch the money path | Superseded (0026) |
| [0019](0019-album-order-record-only.md) | Album order: record the spec, don't integrate or charge | Superseded (0026) |
| [0020](0020-offer-revenue-scorecard.md) | Offer scorecard: a funnel + an honest revenue proxy | Superseded (0026) |
| [0021](0021-aphrodite-products-foundation.md) | Aphrodite products: budget-capped, export-gated foundation (dormant) | Accepted |
| [0022](0022-offer-sku-revenue-attribution.md) | Offer SKU revenue attribution: proxy → real attributed upsell (gated on Plutus SKUs) | Superseded (0026) |
| [0023](0023-album-adopt-seam.md) | Album adopt seam: interlocked production-proposer selection (Mnemosyne flag-flip, baseline default) | Superseded (0026) |
| [0024](0024-money-operations-pane.md) | Money operations: one read-only money/AR pane (past-due AR + collected) | Accepted |
| [0025](0025-b2b-invoicing-essentials.md) | B2B invoicing essentials: PO number + net terms (auto due date) on invoices, company billing details on clients | Accepted |
| [0026](0026-decommission-albums-offers.md) | Decommission the Mnemosyne ALBUMS and Plutus OFFERS subsystems (cut consumer-upsell for the B2B/F&B niche) | Accepted |
| [0027](0027-retainer-quota-lifecycle.md) | Retainer deepening: quota units + per-period snapshot + advisory overage + renewal (term/nudge/pause) | Accepted |
| [0028](0028-retainer-overage-draft-prefill.md) | Retainer overage → draft invoice: assisted editable pre-fill, never an auto-write (§11.4) | Accepted |
| [0029](0029-portal-license-summary.md) | Client-facing licence summary on the portal (active licences, structured, read-only, fee never shown) | Accepted |
| [0030](0030-cull-state-spine.md) | AI-assisted culling: the cull-state spine (operator keep/cut, reversible, audited; flag-gated; deck UI + delivery gate deferred) | Accepted |

See also the operator runbook [`../MISE-SOLO-STUDIO-OS-RUNBOOK.md`](../MISE-SOLO-STUDIO-OS-RUNBOOK.md)
(how to run it day to day), [`../MISE-SOLO-STUDIO-OS.md`](../MISE-SOLO-STUDIO-OS.md),
[`../REPO-CONSOLIDATION-MATRIX.md`](../REPO-CONSOLIDATION-MATRIX.md),
[`../MISE-CONSOLIDATION-ROADMAP.md`](../MISE-CONSOLIDATION-ROADMAP.md),
[`../PHASE-0-SLICE.md`](../PHASE-0-SLICE.md),
[`../NOTION-MODERNIZATION.md`](../NOTION-MODERNIZATION.md).
