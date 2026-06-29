# ADR 0042 - Studio commercial action queue

**Status:** Accepted (F&B/commercial spine; builds on ADR 0041)
**Date:** 2026-06-29
**Deciders:** Kevin (owner), principal engineer

## Context

ADR 0041 made each company command view actionable by ranking the next derived fix for that
relationship. That still requires opening a company to discover the action. The studio Activity page
already owns the morning `Needs attention` strips, so the commercial spine should feed that same
triage surface.

## Decision

Add a read-only `Commercial actions` strip to `/admin/studio/activity`.

- Compute the top company action per root client using the ADR 0041 ranking.
- Sort those company actions by rank and show the most urgent eight.
- Include the strip in the existing needs-attention count on Studio and Activity.
- Link each row to the owning surface plus the company command view.

## Consequences

- The operator can see which commercial relationship needs attention without opening every company.
- No new schema or task lifecycle is introduced; all rows remain derived from invoices, retainers,
  project closeout state, and cadence.
- The Activity page remains the daily triage surface while the company page remains the deeper
  relationship command view.
