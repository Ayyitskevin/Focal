# ADR 0009 — Mnemosyne albums: a deterministic layout validator owns correctness

**Status:** Accepted (albums foundation slice)
**Date:** 2026-06-26
**Deciders:** Kevin (owner), principal engineer

## Context

Mnemosyne is the sibling that proposes **album spreads** — a curated, ordered subset of a
gallery's photos laid out across pages. The 2026-06-25 audit (§11.4) is explicit that an
album proposal is a model output and therefore a *suggestion*, never an authoritative
write: "the model proposes, deterministic code validates." The failure modes that matter
for an album are concrete and unacceptable if they happen silently:

- a photo **duplicated** across spreads,
- a **foreign** photo placed (wrong gallery, a video, or an unfinished asset),
- two photos **misassigned** to the same slot, and
- a photo **silently omitted** — dropped from the album with no signal to the human.

The first three are correctness bugs. The fourth is subtler: omission is the
photographer's editorial right (an album is a *subset* of the take), so it must not fail
validation — but it must never be invisible, or a human approving the draft can't tell a
deliberate cull from a lost photo.

Mnemosyne is not yet integrated. This slice lands only the **foundation** — the
deterministic floor — so the worker and operator UI can be built against a stable,
already-trustworthy contract, exactly the pattern used for the Qwen vision challenger
(seam + validator first, backend later).

## Decision

Make a **pure, deterministic validator the sole authority on album-layout correctness**,
and keep it strictly separate from whatever proposes the layout.

- **`Capability.ALBUMS`** is added to the provider contract. It has **no legacy external
  adapter** and is intentionally absent from the registry's default factories, so
  `resolve(ALBUMS)` raises rather than inventing a production path. A `MockAlbumAdapter`
  exists for tests/shadow only.
- **`app/albums.py`** holds `validate_core(eligible_ids, placements)` (pure, no I/O — the
  function tests pin the invariant against) and a thin `validate_layout(gallery_id, …)`
  wrapper that reads the gallery's eligible photo set first. Eligibility =
  `kind='photo' AND status='ready'` for *this* gallery. The validator reports **every**
  hard issue (not just the first) and always surfaces `omitted`; `ok` is True only when
  there are no hard issues — omission alone never fails.
- **`save_draft` refuses to persist** a layout with any hard issue (or an empty one), and
  writes the draft + its placements in one transaction. A `UNIQUE(album_draft_id,
  asset_id)` constraint (migration 066) is the DB-level backstop for the duplicate case.
- **Drafts are HUMAN_REVIEW state.** `album_drafts.status` starts at `draft`; only a human
  transition reaches `approved`/`rejected`. Nothing here prints, charges, or publishes.
- **Dormant.** Migration 066 is additive and forward-only (two new tables + indexes); no
  existing table changes and nothing in the running app reads or writes them yet.

## Consequences

- **Positive:** the never-omit/duplicate/misassign invariant is enforced by deterministic
  code with exhaustive, structured issues — a future Mnemosyne model can be swapped,
  retrained, or fail entirely without ever producing a bad *stored* album. The validator
  is unit-testable with no DB or network. Rollback is dropping two empty tables.
- **Negative / deferred:** the actual Mnemosyne worker, the proposal-to-`ai_runs`
  provenance hook, and the operator review/approve UI are **not** in this slice. Spread
  *aesthetics* (balance, pacing, facing-page pairing) are out of scope — this validator
  guarantees *integrity*, not taste; quality judgement stays with the human reviewer.
- **Schema:** `album_drafts` / `album_placements` are new; this is the red-light migration
  that gates the slice behind a human-merged PR.

## Alternatives considered

- **Let the model emit a final album directly.** Rejected outright — it is precisely the
  "model writes authoritative state" pattern the audit forbids; a hallucinated or repeated
  asset id would reach print.
- **Treat omission as a hard error.** Rejected — it would make every legitimate cull fail
  validation. Surfacing omission as data (never silent) is the correct middle: visible to
  the human, not blocking.
- **Validate inside a CHECK / trigger only.** The `UNIQUE` constraint is kept as a
  backstop, but DB constraints can't express "eligible photo of this gallery" or surface
  omissions for review, so the authority lives in application code with the constraint as
  defense-in-depth.
