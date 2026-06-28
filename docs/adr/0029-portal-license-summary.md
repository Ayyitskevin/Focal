# ADR 0029 — Client-facing licence summary on the portal

**Status:** Accepted (final slice of the F&B/commercial spine direction; read-only, client-facing)
**Date:** 2026-06-28
**Deciders:** Kevin (owner), principal engineer

## Context

Mise has a sophisticated, market-aware **licensing** model (migration 014): scope, usage tier,
exclusivity, territory, channels, term, status — the F&B/commercial moat. But the **client** only
ever saw a free-text `clients.usage_rights` blob on the portal; the structured licence records
the operator carefully maintained were invisible to the people they govern. For retainer/B2B
clients (restaurants, brands), "what am I actually licensed to do with these images?" is a real,
recurring question that currently lands in email.

This is the last item from the direction synthesis and the **only client-facing** change in the
set, so it is conservative and read-only.

## Decision

Surface the client's **active** licences as a structured "Your usage rights" section on the
portal — a read-only twin of the admin licence list.

- **What's shown.** Per active licence: title, scope, usage tier (humanized), an exclusivity
  flag, channels, territory, and the term (perpetual / dated / open-ended). The **fee is never
  selected or shown** — the client sees what they're licensed for, never the price.
- **Which licences.** `_client_licenses(client_id)` returns the active, non-deleted licences that
  reach this client, deduped across three arms mirroring the admin coverage model: licences the
  client **holds**, a **group** (`holder_and_descendants`) licence held by an ancestor, and a
  **specific** licence that lists this client. Only `status='active'` rows surface —
  draft/expired/terminated/renewed never reach the client.
- **Where.** It renders above the existing free-text `usage_rights` note (kept as a fallback),
  behind the portal's existing PIN gate. No new route, no new schema — a pure read over the
  licences the operator already manages.

## Consequences

- **Positive:** retainer/commercial clients can self-serve "what can I do with this content"
  (channels, territory, term) without an email round-trip; the operator's licensing rigor finally
  shows up where it builds trust.
- **Safe by construction:** read-only, PIN-gated, additive. The fee is excluded by the column
  list (not just hidden in the template). Status filtering keeps anything not-yet-active out of
  the client's view. No migration, no money path, nothing writes.
- **Self-contained:** the coverage walk uses `clients.ancestor_ids` + `license_clients` directly
  in the public module (a local, read-only query) rather than importing the admin licence module,
  avoiding an import cycle.

## Alternatives considered

- **Show every licence reaching the client (any status).** Rejected — exposing draft or
  terminated licences to the client is confusing at best and wrong at worst; active-only is the
  honest "what you hold right now" view.
- **Replace the free-text `usage_rights` note.** Rejected — kept as a fallback for clients with
  no structured licences yet, so the portal never regresses for existing setups.
- **Include the licensing fee / a "renew" CTA.** Rejected for this slice — the fee is the
  operator's business, and renewal is an operator-driven conversation (ADR 0027), not a portal
  self-service action.
