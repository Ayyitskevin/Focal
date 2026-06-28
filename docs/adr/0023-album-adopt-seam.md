# ADR 0023 — Album adopt seam: interlocked production-proposer selection

**Status:** Accepted (album promotion mechanism — the vision-cutover analog for albums)
**Date:** 2026-06-28
**Deciders:** Kevin (owner), principal engineer

## Context

Mise ships a deterministic **baseline** album proposer (`app.albums.propose_layout`, ADR
0009/0011): eligible photos in id order, validated so a layout never omits/duplicates/misassigns
a photo. ADR 0011 said adopting a Mnemosyne backend would be "a registry **registration**, not a
rewrite." But that wasn't literally true: `Capability.ALBUMS` was **absent** from
`registry._DEFAULT_FACTORIES`, so `resolve(ALBUMS)` raised and `albums._provider_placements`
**always** fell back to the baseline — there was no interlock, no flag, and no place to register a
challenger. Vision, by contrast, already had the full cutover seam (ADR 0016): an
`active_vision_provider()` interlock that makes promotion a flag flip.

This ADR brings albums up to the same seam, so adopting Mnemosyne becomes a flag flip — not a
sensitive edit to the proposer path under pressure.

## Decision

Mirror the vision cutover interlock for albums, defaulting to the baseline and staying
byte-identical until a deliberate promotion.

- **Register `Capability.ALBUMS`** → `InternalAlbumBaselineAdapter` (wraps `propose_layout`,
  `serves_production=True`, always enabled). `resolve(ALBUMS)` is now a real adapter, making the
  ADR 0011 "registration, not a rewrite" claim literally true.
- **`registry.active_album_provider()`** — the album analog of `active_vision_provider()`. Reads
  `MISE_ALBUM_PROVIDER` (default `baseline`); a named challenger (Mnemosyne) is honored only if it
  declares `serves_production` **AND** is configured, else it falls back to the baseline and says
  why. So a flag pointing at an unproven/unconfigured proposer can never silently replace the
  baseline.
- **`registry.album_proposer_adapter()`** — the consumer twin: returns the eligible challenger
  adapter or `None` ("use the baseline"). `app.albums._provider_placements` consults it instead of
  `resolve(ALBUMS)`. Default → `None` → the in-app baseline (preserving `per_spread`), **byte-identical**.
- **`InternalAlbumChallengerAdapter`** (Mnemosyne) — registered as the ALBUMS challenger,
  `serves_production=False`, dormant until `MISE_ALBUM_CHALLENGER_URL` is set. It POSTs
  `{gallery_id, asset_ids}` and reads back `albums.schema.json` placements; the deterministic
  validator (`save_draft`) re-checks every placement, so a bad proposal can never store.

## Consequences

- **Positive:** album adoption is now a flag flip (`serves_production=True` + a green pilot +
  `MISE_ALBUM_PROVIDER=mnemosyne`), with the baseline as the permanent default/fallback and the
  validator guarding either source. Behavior today is unchanged (effective proposer is always the
  baseline). No schema, no migration, trivial rollback (flip back).
- **Honest scope:** this is the *interlock + registration*, the album analog of ADR 0016. Two
  pieces remain, both deliberately out of scope here: (a) a real Mnemosyne worker behind the
  challenger URL whose layouts beat the baseline; (b) an **album shadow → ai_runs** runner
  (baseline vs Mnemosyne) feeding a validation gate — the album analog of the vision shadow/gate
  (ADR 0007/0010/0014), the natural next slice.
- **Safety:** the model only proposes; deterministic code validates; a human approves every layout
  before print/export (§11.4). The interlock guarantees a misconfiguration degrades to the
  baseline, never to an unvalidated layout.

## Alternatives considered

- **Register ALBUMS → baseline with no interlock.** Rejected — it fixes the `resolve()` raise but
  leaves no production-vs-challenger gate, so a future challenger would be a code edit on the
  proposer path, not a flag. The interlock is the point.
- **Route the baseline itself through the adapter on the hot path.** Rejected — the baseline
  adapter wraps `propose_layout` with default `per_spread`; the in-module path preserves the
  caller's `per_spread`, so the hot path stays on `propose_layout` and the adapter exists for
  facade/resolve consistency.
- **Build the Mnemosyne worker + shadow gate now.** Deferred — larger, and gated on a real
  Mnemosyne endpoint and a human-scored pilot; the seam is the prerequisite and ships first
  (exactly as vision did).
