# ADR 0041 - Company next-action ranking

**Status:** Accepted (F&B/commercial spine; builds on ADRs 0034, 0039, 0040)
**Date:** 2026-06-29
**Deciders:** Kevin (owner), principal engineer

## Context

The company command view now rolls up money, retainers, cadence, licences, projects, and
deliverable progress. Project closeout also makes the per-project gaps visible. The remaining daily
friction is prioritisation: when a company has overdue AR, a draft invoice, an unfinished project,
and a repeat-shoot nudge, the operator still has to scan the whole page to decide what to touch
first.

## Decision

Add a derived, read-only `Next actions` strip to the company command view.

- Rank company-level money first: past-due invoices, then the newest draft invoice.
- Rank retainer quota gaps and the first closeout gap for each active project next.
- Rank repeat-client cadence nudges after current obligations.
- Each action links to the existing owning surface; no task table, status mutation, send, charge,
  publish, or close action is created.

## Consequences

- The company page becomes a practical daily command view instead of only a reporting page.
- The ranking remains deterministic and easy to adjust in code as the commercial spine evolves.
- Because actions are derived, stale tasks cannot accumulate and rollback is just removing the
  helper plus template panel.
