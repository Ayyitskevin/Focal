# ADR 0039 — Commercial recon gaps + project closeout readiness

**Status:** Accepted (F&B/commercial spine; builds on ADRs 0034–0038)
**Date:** 2026-06-29
**Deciders:** Kevin (owner), principal engineer

## Context

After PR #77 merged, the commercial project record was coherent end-to-end:
shot list → deliverable spec → licence → invoice, with company-level roll-ups.
The remaining recon gaps were small but high-fit:

- repeat-client cadence: last shoot + typical interval → "due for a shoot" nudges
- commercial shot-list templates: clone a canned intake list into a project
- a project-level closeout view: one place to see whether money, rights, delivery,
  and workspace are reconciled before closing the shoot

## Decision

Ship these as deterministic, read-only or operator-triggered slices with no new schema.

- **Repeat-client cadence.** `app/admin/common.py::shoot_cadence()` derives cadence from past
  `projects.shoot_date` values over a client/company group. It uses the median interval, suppresses
  due nudges when a future shoot is already scheduled, and surfaces compact cues on the company view
  and client list. It writes nothing.
- **Shot-list templates.** `usage_vocab.SHOT_TEMPLATES` holds app-level canned commercial lists
  (`Hero + detail`, `Menu 3-part`). `/admin/studio/projects/{id}/shots/template` clones a template
  into normal `shot_list` rows, appending after existing `sort_order` and auditing every row.
- **Closeout readiness.** `studio._project_closeout()` builds a read-only checklist on the project
  page: shot list, deliverables, usage licence, invoice, open AR, gallery, and workspace. Each row
  links back to the surface that owns the fix. It never sends, charges, publishes, or changes status.

## Consequences

- The operator gets useful nudges without another workflow table or hidden state.
- Template clones remain editable project data; they are not a second template-instance system.
- Closeout is a deterministic dashboard, not an automation gate. It can later feed a company-level
  "next action" strip or a delivery gate, but this slice deliberately stops at visibility.
- Continuation point: keep the money/rights boundary intact. The next natural slices are
  paired deliverable templates and a company-level "next action" ranking, both read-only/assisted.

## Alternatives considered

- **Persist cadence settings per client.** Rejected for v1. Derived cadence is enough to expose the
  repeat-client gap and avoids another client preference UI.
- **Template tables.** Rejected for v1. The first templates are stable app vocabulary; if Kevin starts
  editing them frequently, a schema can follow.
- **Auto-close a project when all checks pass.** Rejected. Closeout readiness is information; closing
  remains a human stage change.
