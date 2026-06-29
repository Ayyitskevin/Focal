# ADR 0045 - Company communication history

**Status:** Accepted (F&B/commercial spine; builds on ADRs 0043, 0044)
**Date:** 2026-06-29
**Deciders:** Kevin (owner), principal engineer

## Context

The company command view now tells the operator which commercial relationship needs action and
whether past-due AR was chased recently. The remaining friction is context: before sending another
document or AR follow-up, the operator needs to see what has already been sent across the company
and its child venues without opening each project.

## Decision

Add a read-only communication history section to the company command view.

- Roll up recent proposal, contract, and invoice emails whose projects belong to the company group.
- Include company-level AR chases from `emails_log` rows marked by the AR assist subject prefix.
- Link each row back to the owning document, AR chase page, and project when available.
- Exclude unrelated catch-all `other` emails, such as gallery delivery sends, from this commercial
  company history.

## Consequences

- The company view now carries the recent outbound trail needed before another commercial nudge.
- No schema, send, invoice, project, or task state changes are introduced.
- The global sent-email log remains the full audit surface; the company view is a scoped commercial
  summary.
