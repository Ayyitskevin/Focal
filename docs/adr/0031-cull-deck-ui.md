# ADR 0031 — AI-assisted culling: the keyboard cull deck (UI over the cull-state spine)

**Status:** Accepted (PR-B of the scoped culling plan; builds on ADR 0030)
**Date:** 2026-06-28
**Deciders:** Kevin (owner), principal engineer

## Context

ADR 0030 shipped the durable cull-state spine + write routes (keep/cut/restore, reversible,
audited, flag-gated) with no UI. This is the operator surface those routes were built for: the
**keyboard cull deck** the operator chose in the design workflow — fast keep/cut over the keeper
scores the vision sidecars already compute (`argus_keeper_score`, migration 064). It stays behind
the same `MISE_CULL_UI` flag, so the whole feature is still inert until the operator arms it.

The score it ranks on is **source-agnostic**: cloud Argus writes it today, a future local Qwen
pass writes the same column — promoting local scoring later needs zero deck change (the local-AI
endgame the operator chose). This PR does not touch the client-delivery path; a 'cut' frame is
still delivered until the delivery gate (PR-C) ships, and the deck says so loudly.

## Decision

A self-contained admin page + a small vanilla controller, no new front-end dependency.

- **Deck route (`GET …/galleries/{id}/cull`).** Renders every *ready photo* in the gallery ranked
  best-first by keeper score (unscored last, in capture order), with live keep/cut/undecided
  counts. Flag-gated (404 when off). Read-only render — every decision posts to the 0030 routes.
- **Large-preview route (`GET …/cull/preview/{id}`).** Serves the screen-sized `web` derivative
  for the focused card (admin-only, flag-gated). Mirrors the existing `admin_thumb` serve but the
  larger variant; **never the original** — the deck does no full-res serve. The triage grid reuses
  the existing `admin_thumb` route for its lazy-loaded thumbnails.
- **Snappy writes (204 on `HX-Request`).** A decision from the deck is a same-origin `fetch` that
  sends `HX-Request`; the write routes answer it with an empty **204** so the deck never reloads
  per keystroke. A plain form POST (no header — the JS-off fallback and every test) still gets the
  **303** back to the gallery. Backward-compatible with the 0030 tests.
- **The deck (`templates/admin/cull.html` + `static/cull_deck.js`).** One big card at a time;
  keys **K** keep, **X** cut, **H/→** skip, **←** back, **R** undecide, **U** undo last (a real
  undo stack that re-asserts the prior state on the server). A **score-threshold slider**
  pre-selects the low-scoring tail for one bulk `cut`. A **triage grid** shows every frame with its
  decision (and a dimmed/outlined 'cut'), click to jump. CSRF is origin-based, so the `fetch`
  posts need no token; HTMX is already global, so no new dependency ships.
- **Entry point.** A "Cull" link on the gallery page, shown only when the flag is on and only for
  galleries (not transfers).

## Consequences

- **Positive:** the high-value AI capability the operator actually wanted — fast, keyboard-first
  culling over the scores Mise already computes — exists and is revertible behind one flag.
- **§11.4 holds:** AI only *ranks*; every keep/cut is an explicit human keystroke; nothing
  auto-deletes or auto-publishes. Decisions stay reversible and audited (via the 0030 routes).
- **No delivery change:** a 'cut' frame is still visible to clients until PR-C; the deck banner
  states this plainly so the operator is never surprised.
- **Scale:** the deck embeds the gallery's photo metadata (compact JSON) and lazy-loads
  thumbnails; no server pagination. Fine for shoot-sized galleries; if libraries grow into the
  many-thousands a paginated/virtualised grid is the follow-up (logged, not silently capped).

## Alternatives considered

- **Server round-trip per decision (full-page or HTMX swap).** Rejected for the deck — too slow
  for keyboard culling; the 204 + client-side queue keeps each keystroke instant while the server
  stays authoritative.
- **Add Alpine.js for the interactivity.** Rejected — a ~120-line vanilla controller covers the
  keyboard/undo/threshold needs with no new dependency and no CSP change (external `/static` JS).
- **Worst-first ordering.** Rejected as the default — best-first pairs with the threshold sweep
  (review keepers, bulk-cut the low tail) and is a reversible UI default, not a data decision.
- **Cull video too.** Out of scope — keeper scoring and rapid culling are a photo workflow; the
  deck filters to `kind='photo'`.
