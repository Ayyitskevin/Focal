# ADR 0040 — Project deliverable templates

**Status:** Accepted (F&B/commercial spine; builds on ADR 0038)
**Date:** 2026-06-29
**Deciders:** Kevin (owner), principal engineer

## Context

Project deliverable specs made the contracted "what we owe" first-class, but intake still required
re-keying the same commercial patterns: menu stills, hero + reels, print + web. That friction makes
the closeout/readiness view less useful because the operator still has to build every deliverable
line from scratch.

## Decision

Add app-level `DELIVERABLE_TEMPLATES` in `usage_vocab.py` and a project-page clone action.

- Templates are stable code vocabulary for now: `Menu stills`, `Hero + reels`, and `Print + web`.
- Cloning a template inserts normal `project_deliverables` rows, appending after existing
  `sort_order`, with `delivered_qty=0`.
- Every created row is audited as `entity_type='project_deliverable'`.
- The operator can edit or delete cloned rows like any manually-created deliverable.

## Consequences

- Intake can create a reasonable deliverable spec in one click without adding schema or a second
  template-instance model.
- This remains operator-controlled. It never delivers files, charges, sends, publishes, or changes a
  project status.
- If Kevin starts editing templates frequently, a table-backed custom template system can replace the
  app vocabulary later without changing the stored deliverable rows.
