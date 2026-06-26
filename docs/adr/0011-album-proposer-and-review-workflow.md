# ADR 0011 — Mnemosyne albums: a deterministic baseline proposer + human review workflow

**Status:** Accepted (albums worker + review UI)
**Date:** 2026-06-26
**Deciders:** Kevin (owner), principal engineer

## Context

ADR 0009 shipped the **albums foundation** — the `album_drafts`/`album_placements` schema
and the deterministic layout validator (`app/albums.validate_*`) that guarantees an album
draft never silently omits, duplicates, or misassigns a photo. It was dormant: there was
no way to *create* a draft or *review* one.

The audit's loop is "the model proposes, deterministic code validates, a human approves"
(§11.4). Two pieces were missing: the **proposer** (the thing that emits a draft) and the
**review** surface (where a human approves or rejects it). The real Mnemosyne model isn't
integrated yet — and, exactly as with vision (ADR 0007, where the shadow harness shipped
before the Qwen backend), it shouldn't block the lifecycle. A studio needs a *starting*
album layout to react to even before any model exists, and that starting point can be
produced deterministically.

## Decision

Ship a **deterministic baseline proposer** plus the **human review workflow**, with the
provider seam in place for a future Mnemosyne model.

- **Proposer.** `albums.propose_layout` is pure: eligible photos (`kind='photo' AND
  status='ready'`) in id order, `per_spread` slots per spread. `albums.propose_draft`
  orchestrates it: it prefers a **registered ALBUMS provider** (the seam a Mnemosyne
  backend plugs into via `registry.resolve(Capability.ALBUMS)`) and falls back to the
  deterministic baseline when none is registered. **Either source's output is handed to
  `save_draft`'s validator**, so a model proposal that would omit/duplicate/misassign can
  never become a stored draft. Provenance is recorded to `ai_runs` (capability `albums`),
  best-effort — a ledger failure never blocks the draft.
- **Review workflow.** `/admin/albums` is a draft queue (filterable by status); the detail
  page renders the spreads as **photo thumbnails** (reusing the existing
  `/admin/thumb/{gallery}/{asset}` route), surfaces the **omitted** photos, and **re-runs
  the validator against the gallery's *current* photos at view time** — so a placement
  whose asset was deleted/unpublished since proposal shows up as an issue instead of
  hiding. Approve/reject are explicit human POSTs that set `album_drafts.status`.
- **Bounded authority.** Approval records a human decision and nothing more — it does
  **not** print, order, or charge for an album. No money/contract state is touched. No
  schema change: the tables and the `status` CHECK shipped in ADR 0009/migration 066.

## Consequences

- **Positive:** the albums foundation becomes a usable, end-to-end loop (propose → review →
  approve/reject) without waiting on a model, and without ever risking a corrupt stored
  album — the validator is the floor under both the baseline and any future model. The
  Mnemosyne backend, when it arrives, is a registry registration, not a rewrite. Re-running
  the validator at view time makes a stale draft self-evident.
- **Negative / deferred:** the baseline proposer optimizes nothing about *aesthetics*
  (pacing, facing-page pairing, hero emphasis) — it guarantees *integrity*, not taste; that
  is exactly the judgement the human reviewer (and later a Mnemosyne model graded on the
  validation harness, ADR 0010) supplies. In-UI reordering/editing of a draft's layout, and
  the actual album *order/print* flow, are separate later slices (the latter is red-light:
  it touches fulfilment/money).
- **Operational:** album proposals now appear in the `ai_runs` ledger and at
  `/admin/ai-runs` under a new `albums` filter.

## Alternatives considered

- **Wait for the Mnemosyne model before building review.** Rejected — it leaves the
  foundation dormant indefinitely and couples the review UI's delivery to a model
  integration. The deterministic baseline is genuinely useful and de-risks the model work.
- **Let the proposer write an approved album directly.** Rejected — it skips the human and
  the audit's review gate. Every draft starts at `draft`; only a human POST approves.
- **Auto-propose on gallery publish (like the Argus/Plutus hooks).** Deferred — an
  operator-triggered "Propose" keeps the feature dormant and predictable for now; an
  automatic hook can be added behind a flag once the workflow has proven out.
- **Skip re-validation on view (trust the stored draft).** Rejected — assets can be deleted
  or unpublished after a draft is stored; re-validating at view time keeps the
  never-silently-wrong invariant true at the moment of human decision, not just at proposal.
