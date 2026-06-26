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
| [0009](0009-album-layout-deterministic-validator.md) | Mnemosyne albums: a deterministic layout validator owns correctness | Accepted |
| [0010](0010-validation-scoring-promotion-gate.md) | Validation-scoring harness: a deterministic promotion gate | Accepted |
| [0011](0011-album-proposer-and-review-workflow.md) | Mnemosyne albums: deterministic baseline proposer + human review workflow | Accepted |
| [0012](0012-offer-approval-state.md) | Plutus offers: persisted operator approve/reject state | Accepted |
| [0013](0013-ai-operations-dashboard.md) | AI operations dashboard: one read-only pane over the consolidated capabilities | Accepted |
| [0014](0014-shadow-to-validation-bridge.md) | Shadow→validation bridge: enrol shadowed galleries into the gate from the ledger | Accepted |

See also [`../MISE-SOLO-STUDIO-OS.md`](../MISE-SOLO-STUDIO-OS.md),
[`../REPO-CONSOLIDATION-MATRIX.md`](../REPO-CONSOLIDATION-MATRIX.md),
[`../MISE-CONSOLIDATION-ROADMAP.md`](../MISE-CONSOLIDATION-ROADMAP.md),
[`../PHASE-0-SLICE.md`](../PHASE-0-SLICE.md).
