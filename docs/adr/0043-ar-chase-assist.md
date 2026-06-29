# ADR 0043 - AR chase assist

**Status:** Accepted (F&B/commercial spine; builds on ADRs 0036, 0041, 0042)
**Date:** 2026-06-29
**Deciders:** Kevin (owner), principal engineer

## Context

The company command view and Studio Activity queue can now point at past-due AR, and ADR 0036 added
per-company statements. The final chase step was still scattered: open the statement, open each
invoice, copy links, write a reminder, then send from a different surface.

## Decision

Add a company-level AR chase assist at `/admin/studio/companies/{id}/ar-chase`.

- Derive overdue rows from issued invoices whose due date is past on the studio wall-clock and whose
  balance remains open after recorded payments.
- Show the company statement, CSV export, admin invoice rows, and client payable invoice links in
  one review surface.
- Pre-fill a concise email draft to the billing contact, including overdue invoice links and the
  total open balance.
- Send only after the operator submits the form. The send uses the existing mailer and records one
  `emails_log` row with `doc_kind='other'`.
- Link the assist from the company action strip, Studio Activity's commercial action queue, the
  company past-due section, and overdue invoice detail pages.

## Consequences

- Past-due commercial AR now has a single review/send workflow instead of a manual copy-and-paste
  path.
- No invoice, payment, statement, project, licence, or task state is mutated by opening or sending
  the assist.
- The overdue calculations for company view, Activity, and the assist now share one helper, reducing
  drift between triage and follow-up.
