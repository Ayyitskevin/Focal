# docs/ — index

A map of this folder so a reader (human or agent) can tell what's canonical reference vs.
forward-looking plan vs. point-in-time artifact. **Context:** Mise is a product-incubation
sandbox — **not deployed, no live users** (see the root `README.md`). The launch/go-live docs
below describe *intended* hosted state, not something currently serving customers.

## Architecture & decisions (canonical reference)
- [`adr/`](adr/) — 70 Architecture Decision Records; the durable "why" behind the design. `SECURITY.md` cites specific ADRs, so they're load-bearing.
- [`SECURITY.md`](SECURITY.md) — security playbook (auth, sessions, CSP, secrets).
- [`IOS-ARCHITECTURE.md`](IOS-ARCHITECTURE.md) — iOS app architecture and delivery plan.
- [`IOS-API-V1.md`](IOS-API-V1.md) — mobile API v1 contract.

## Product
- [`MISE-SOLO-STUDIO-OS.md`](MISE-SOLO-STUDIO-OS.md) — the "solo studio OS" product concept.
- [`MISE-SOLO-STUDIO-OS-RUNBOOK.md`](MISE-SOLO-STUDIO-OS-RUNBOOK.md) — operator runbook for it.
- [`NICHE-STORY-DECISION.md`](NICHE-STORY-DECISION.md) — T10 decision packet comparing wedding-first, F&B-first, and neutral launch stories; Kevin's selection unblocks the reviewer demo and store copy.

## Launch & hosting plans  _(status: intent — not deployed)_
- [`CONDUCTOR-PLAN.md`](CONDUCTOR-PLAN.md) — **start here for execution**: mission review, binding process rules from the 2026-07-17 red-main postmortem, live board, and ticket-by-ticket work orders (T1–T10) for the Opus/Sonnet/Kevin lanes.
- [`APP-STORE-GAMEPLAN.md`](APP-STORE-GAMEPLAN.md) — the strategy layer under the conductor plan: phased App Store / micro-SaaS plan with audit evidence (decision base ADR 0070).
- [`GO-LIVE.md`](GO-LIVE.md) — day-of go-live sequence.
- [`BETA-LAUNCH.md`](BETA-LAUNCH.md) — hosted beta launch plan.
- [`LAUNCH-KIT.md`](LAUNCH-KIT.md) · [`LAUNCH-PLAYBOOK.md`](LAUNCH-PLAYBOOK.md) — $20 hosted launch assets & phases.
- [`SAAS-DEPLOYMENT.md`](SAAS-DEPLOYMENT.md) — hosted SaaS deployment guide.
- [`RELEASE-NOTES.md`](RELEASE-NOTES.md) — v1.0-beta release notes.
- [`SUPPORT-PLAYBOOK.md`](SUPPORT-PLAYBOOK.md) — beta support playbook.
- [`IOS-UPGRADE.md`](IOS-UPGRADE.md) — iOS upgrade plan.
- [`APP-STORE-SUBMISSION.md`](APP-STORE-SUBMISSION.md) — App Store submission pack (privacy labels, reviewer access, archive checklist).

## Fleet / consolidation planning
- [`MISE-CONSOLIDATION-ROADMAP.md`](MISE-CONSOLIDATION-ROADMAP.md) · [`REPO-CONSOLIDATION-MATRIX.md`](REPO-CONSOLIDATION-MATRIX.md) — how Mise consolidates with sibling repos.
- [`SIBLING-IMPROVEMENT-PLAN.md`](SIBLING-IMPROVEMENT-PLAN.md) · [`WORKER-CONTRACT.md`](WORKER-CONTRACT.md) · [`sibling-briefs/`](sibling-briefs/) — making each repo a useful "OS worker."
- [`NOTION-MODERNIZATION.md`](NOTION-MODERNIZATION.md) — Notion API modernization runbook.

## Point-in-time artifacts  _(historical — a dated snapshot, not live state)_
- [`MISE-REVIEW.md`](MISE-REVIEW.md) — full-app review (2026-07-12).
- [`HANDOFF-QUEUE.md`](HANDOFF-QUEUE.md) · [`SESSION-HANDOFF.md`](SESSION-HANDOFF.md) — execution queue / session handoff from that review.
- [`PHASE-0-SLICE.md`](PHASE-0-SLICE.md) — Phase 0 foundation slice notes.
