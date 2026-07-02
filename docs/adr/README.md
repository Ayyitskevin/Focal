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
| [0031](0031-cull-deck-ui.md) | AI-assisted culling: the keyboard cull deck (UI over the spine — score-ranked deck, K/X/H/U, threshold sweep, large preview; flag-gated; no delivery change) | Accepted |
| [0032](0032-cull-delivery-gate.md) | AI-assisted culling: the client-delivery gate (a cut frame stops being listed/served/zipped/portal-shown; flag-gated rollback; public-site & transfers excluded) | Accepted |
| [0033](0033-local-keeper-scorer.md) | Local keeper-scorer for culling (per-asset Qwen scores into argus_keeper_score; score-only, asset_id-keyed, own flag; decoupled from the Argus→Qwen production cutover) | Accepted |
| [0034](0034-company-command-view.md) | Per-company command view (read-only group roll-up: MRR + retainer utilisation, AR + overdue, pipeline, active licences, shoot cadence over client+venues) | Accepted |
| [0035](0035-recurring-revenue-forecast.md) | Recurring-revenue forecast (studio-wide MRR/ARR + pure 12-month projection honouring pause-at-term + renewals due; read-only) | Accepted |
| [0036](0036-ar-aging-and-statements.md) | AR aging buckets (30/60/90+, pure bucketer) on money-ops + per-company statement (issued invoices + payments over a range, CSV export); read-only | Accepted |
| [0037](0037-license-invoice-coupling.md) | Licence ↔ invoice coupling (licenses.invoice_id; grant a stub licence from the invoice page → existing editor; money path untouched; visible on invoice + company view) | Accepted |
| [0038](0038-project-deliverable-specs.md) | Project deliverable specs (per-shoot contracted deliverables: count/unit/format + manual delivered count; project panel + company-view roll-up; mirrors the shot-list module) | Accepted |
| [0039](0039-commercial-recon-gaps-and-closeout.md) | Commercial recon gaps + closeout readiness (derived repeat-client cadence, canned shot-list templates, project closeout checklist; no new schema, no automation) | Accepted |
| [0040](0040-project-deliverable-templates.md) | Project deliverable templates (clone canned commercial deliverable specs into normal audited project_deliverables rows; no schema, no automation) | Accepted |
| [0041](0041-company-next-actions.md) | Company next-action ranking (read-only derived action strip over AR, drafts, retainers, project closeout gaps, and cadence; no task table or automation) | Accepted |
| [0042](0042-studio-commercial-action-queue.md) | Studio commercial action queue (top derived company action per root client on Activity; no task lifecycle or automation) | Accepted |
| [0043](0043-ar-chase-assist.md) | AR chase assist (company-level review/send draft for past-due invoices, linked from commercial actions and invoices; no invoice/payment mutation) | Accepted |
| [0044](0044-ar-chase-cadence.md) | AR chase follow-up cadence (derived from manual send log; distinguishes never chased, recently chased, and due follow-ups; no automation) | Accepted |
| [0045](0045-company-communication-history.md) | Company communication history (read-only company-group sent-email roll-up for proposal/contract/invoice sends and AR chases; no schema or automation) | Accepted |
| [0046](0046-company-billing-readiness.md) | Company billing readiness (read-only company-group AP profile gaps plus action when draft/past-due invoices lack AP email; no schema or automation) | Accepted |
| [0047](0047-microsaas-instance-per-customer.md) | MicroSaaS positioning: instance-per-customer, one flat $20/mo price, self-host free; rejects a row-level multi-tenant rewrite (physical isolation is the moat) | Accepted |
| [0048](0048-tenant-bound-admin-sessions.md) | Tenant-bound admin sessions (hosted auth isolation): the admin cookie is bound to the serving host's principal — `admin` / `tenant:<slug>` / `operator` — closing cross-tenant and tenant→operator escalation; single-tenant unchanged | Accepted |
| [0049](0049-hosted-client-payment-isolation.md) | Hosted client-payment isolation (fail-closed, per-tenant Stripe): client invoices charge the tenant's own Stripe, never the operator's platform key; preflight tripwire; single-tenant unchanged | Accepted |
| [0050](0050-hosted-billing-lifecycle-integrity.md) | Hosted billing-lifecycle integrity: exactly-once SaaS webhooks (marker + effect in one transaction), past_due dunning grace instead of instant lockout, per-IP signup throttle | Accepted |
| [0051](0051-hosted-recovery-and-ownership.md) | Hosted recovery & ownership: stateless single-use password reset, one-click full-studio export (consistent DB snapshot + media), self-serve delete (cancels billing, tombstones slug, trash-parks data) | Accepted |
| [0052](0052-hosted-trust-pages.md) | Hosted trust pages: public /terms, /privacy, /support at the platform root (one template, product-accurate copy incl. no-AI-training promise), footer + signup consent links; content-only, no red-light surface | Accepted |
| [0053](0053-hosted-beta-gate-and-welcome-email.md) | Hosted beta gate + welcome email: MISE_SAAS_INVITE_CODE gates signup (constant-time, pre-provisioning; unset = public), deferred welcome email carries the studio URL on both checkout exits, login honors ?trial=1 confirmation | Accepted |
| [0054](0054-tenant-self-serve-stripe-connection.md) | Tenant self-serve Stripe connection (BYO keys): Account panel writes the ADR 0049 fail-closed columns — both key + webhook secret required, live-verified before save, masked render, one-click fail-closed disconnect; Connect remains the later upgrade | Accepted |
| [0055](0055-tenant-email-identity-and-integration-isolation.md) | Tenant email identity + integration isolation: studio-name From + owner Reply-To via one mailer seam, leads/booking copies route to the tenant owner, tenant-host booking links, and operator Notion/GCal/SMS fail closed in tenant contexts | Accepted |
| [0056](0056-hosted-checkout-recovery.md) | Hosted checkout recovery: POST /admin/billing/checkout (re)starts the $20 subscription for no-sub or canceled tenants — remaining trial days carry over, spent trials bill at once, live subs refused; shared session helper with signup | Accepted |
| [0057](0057-hosted-backups-and-ci-hosted-suite.md) | Hosted backups (compose sidecar: integrity-checked per-tenant DB snapshots + retention + optional rclone off-site of snapshots and media + heartbeat marker wired to the ops alarm + restore drill) and the hosted test suite joins the CI unit gate | Accepted |
| [0058](0058-proxy-aware-client-ip.md) | Proxy-aware client IP: forwarded headers trusted only from our own ingress (MISE_TRUSTED_PROXY_CIDRS; CF-Connecting-IP, then Caddy's rightmost XFF) — fixes globally-shared rate limits/PIN lockout behind the compose proxy without reintroducing spoofing | Accepted |

See also the operator runbook [`../MISE-SOLO-STUDIO-OS-RUNBOOK.md`](../MISE-SOLO-STUDIO-OS-RUNBOOK.md)
(how to run it day to day), [`../MISE-SOLO-STUDIO-OS.md`](../MISE-SOLO-STUDIO-OS.md),
[`../REPO-CONSOLIDATION-MATRIX.md`](../REPO-CONSOLIDATION-MATRIX.md),
[`../MISE-CONSOLIDATION-ROADMAP.md`](../MISE-CONSOLIDATION-ROADMAP.md),
[`../PHASE-0-SLICE.md`](../PHASE-0-SLICE.md),
[`../NOTION-MODERNIZATION.md`](../NOTION-MODERNIZATION.md).
