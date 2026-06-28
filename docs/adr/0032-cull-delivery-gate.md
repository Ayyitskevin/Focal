# ADR 0032 — AI-assisted culling: the client-delivery gate (a cut frame stops reaching clients)

**Status:** Accepted (PR-C of the scoped culling plan; completes ADRs 0030 / 0031)
**Date:** 2026-06-28
**Deciders:** Kevin (owner), principal engineer

## Context

ADR 0030 made `cut` a reversible, audited record and ADR 0031 built the deck to set it — but both
were explicit that v1 keeps `cut` a *pure record*: a cut frame was still delivered to clients.
This ADR is the enforcement half the operator asked for after living with the deck: a cut frame is
no longer listed, served, zipped, or shown on the portal. It is the one **red-light** change of the
three (it edits live client-facing serving paths), so it shipped as its own reviewed PR.

## Decision

A single gate helper (`app/delivery_gate.py`) and a consistent SQL fragment applied at every
client read of `assets`. NULL (undecided) and `keep` always deliver; only `cut` is withheld.

- **One helper, one rule.** `delivery_gate.clause(alias)` returns `" AND <alias>.cull_state IS NOT
  'cut'"` (SQLite `IS NOT` is NULL-safe, so NULL/keep pass in one expression) — or `""` when the
  gate is off. The fragment carries no bind parameters, so it never shifts a query's `?` order.
- **Flag-gated enforcement (`MISE_CULL_UI`).** The *same* switch gates the deck and the gate, so
  the whole feature is one env var: flip it off and client delivery returns to the pre-cull path
  exactly — the strangler-rollback invariant. The deck only ever sets `cut` while the flag is on,
  so in practice behaviour is identical; the flag just keeps rollback clean and the feature
  dormant-until-armed like its siblings.
- **Surfaces gated (client delivery only):** the gallery listing, the favourite lookup/proofing
  counts, the video-comment asset gate (`gallery.py`); the media serve + video poster (`media.py`,
  so a guessed/cached URL 404s); single-file / favourites / section downloads (`downloads.py`); the
  full-gallery ZIP builder (`jobs._h_zip`); and the portal crops list, favourites summary, crops
  ZIP, and the `_client_asset` serve chokepoint for portal thumb/crop (`portal.py`).
- **Cache invalidation.** The full-gallery ZIP is cached by `galleries.content_rev`; a cull
  decision now bumps `content_rev` (`cull.py`), so the next download rebuilds through the gate. The
  favourites/section ZIPs are keyed by a content hash of the (now-gated) id list, so they
  self-invalidate; the listing/media/per-file paths filter live and need no invalidation.

## Consequences

- **The feature is now whole:** the rejects an operator cuts don't ship — the actual payoff of
  culling. Everything stays reversible (a `restore` re-delivers) and audited.
- **§11.4 holds:** AI only ranks; the human cut is what hides the frame; nothing is deleted.
- **Rollback:** one env var (`MISE_CULL_UI=false`) returns every client path to pre-cull delivery.
  Note the full-gallery ZIP reflects the cull state at its last build (a flag toggle alone doesn't
  force a rebuild); the live paths honour the flag immediately. Documented, not silently capped.
- **Boundary — the public marketing site is NOT gated.** `site.py` shows only `portfolio=1` assets,
  a separate, explicit publication intent set by the operator; entangling it with a client-cull
  decision would surprise the operator (their portfolio piece vanishing because they cut it in a
  client cull). Portfolio stays the axis that governs the public site; cull governs client delivery.
- **Transfers (drops) are NOT gated.** A transfer is a literal "send these files"; the cull deck is
  gallery-only, so a drop has no cut frames to hide.

## Alternatives considered

- **Unconditional enforcement (cut always hidden, regardless of flag).** Rejected — it breaks the
  one-env-var rollback the repo's strangler invariant calls for, and would leave cut frames hidden
  with no UI to restore them when the flag is off. Flag-gating keeps the whole feature coherent.
- **Gate at serve time only (not in listings).** Rejected — a listed-but-unservable frame is a
  broken tile; gating the listings too is the honest client experience.
- **Filter in Python after the query.** Rejected — easy to miss a path and pays to fetch rows just
  to drop them; a SQL fragment at each read is auditable and uniform.
- **Bump nothing and let the ZIP rebuild on next content change.** Rejected — a cut frame would
  linger in the cached deliverable ZIP indefinitely; the `content_rev` bump invalidates it now.
