# Mobile inquiry-to-client/project conversion specification

**Status:** Implemented locally; backend verified, macOS/XCTest and independent review pending  
**Date:** 2026-11-17  
**Depends on:** `INQUIRY-MOBILE-TRIAGE.md`

## Review findings

The browser's `/admin/studio/inquiries/{id}/client` action already converts an
inquiry into a client and, for dated booking inquiries, a project. It also provides
the canonical admin detail pages for those records. Native inquiry detail has the
converted IDs but no conversion command or way to open the resulting records.

The native contract must strengthen two behaviors before exposing that action:

- conversion needs an atomic idempotency guard so a retry cannot create duplicate
  clients/projects or rewrite conversion history;
- a blank inquiry email must not match an unrelated client whose stored email is
  also blank. Only a trimmed, non-empty exact email match is eligible for reuse.

## Goal

Let a write-scoped owner convert an open inquiry on iOS and navigate to the resulting
client/project without duplicating records on retry. Preserve the browser's useful
creation semantics while making the new HTTP operation atomic, explicit, and safe.

## Non-goals

- Quick quotes, proposals, invoices, payments, signatures, or pricing.
- Email/SMS replies or notification delivery.
- Conversion undo or deleting created clients/projects.
- New native client/project detail pages or new native read endpoints for them.
- Schema/migration, authentication, APNs, or deployment changes.
- Refactoring the existing browser route. Its behavior is the compatibility baseline,
  not authorization for an unrelated browser rewrite.

## API contract

### `PUT /api/v1/inquiries/{inquiry_id}/conversion`

Requires the exact owner principal with `studio:write`. The operation ensures the
inquiry is converted and returns the current `InquiryDetail` with `200` and
`Cache-Control: no-store`.

The target URI identifies the conversion resource, so both the first real transition
and idempotent repeats return `200`; no `Location` header is required. Repeating the
command after an ambiguous transport outcome returns the current converted state
without creating another record. No separate idempotency key is required because the
stored conversion state is the natural idempotency boundary.

State transitions:

1. Acquire SQLite `BEGIN IMMEDIATE` so concurrent conversions serialize.
2. Return an already-converted inquiry unchanged: no new timestamp, client, project,
   or audit row.
3. Refuse a dismissed, unconverted inquiry with `409 inquiry.dismissed` and no writes.
4. Find an existing client only by the inquiry's trimmed, non-empty exact email.
   Blank emails never match. Reuse that client or create one with the inquiry's name,
   business, email, and original message context.
5. For a `booking` inquiry with a shoot date, create one project titled
   `{service or "Shoot"} — {shoot_date}` using the schema's `inquiry_received`
   default. Other inquiry kinds create no project.
6. Stamp `converted_at` and both converted IDs and append one `convert` audit row as
   actor `owner` in the same transaction. The audit diff records the old/new converted
   IDs, not contact or message bodies.

Unknown IDs return `404 inquiry.not_found`; invalid IDs use the existing bounded
`422` validation contract. A converted inquiry's `converted_project_id` remains null
when its semantics do not create a project; null is intentional and distinct from a
missing response field.

No command sends messages, creates a proposal, or deletes any existing record.

## iOS behavior

- Open inquiries show **Convert to client / project** beside the existing dismissal
  control. Conversion requires confirmation and lists the record-creation effects.
- Dismissed inquiries must be restored before conversion; converted inquiries show
  no conversion control.
- A successful response immediately updates the detail and refreshes inquiries,
  dashboard, clients, and projects. Failure preserves the last confirmed detail and
  caches. The repository rejects a success payload that is not converted or lacks a
  converted client ID.
- Converted client/project IDs render **Open client record** and **Open project
  record** links to the canonical `/admin/studio/clients/{id}` and
  `/admin/studio/projects/{id}` pages on the current workspace origin. The links are
  explicitly labeled as opening the web admin; they do not pretend native detail
  pages exist or transfer the native bearer session into a browser cookie.
- Repository success invalidates only inquiry, dashboard, client, and project
  snapshots. Unrelated snapshots remain untouched.

## Acceptance criteria

1. A booking inquiry creates one client and one correctly titled/dated project and
   returns their IDs. A contact inquiry creates only a client.
2. An existing trimmed, non-empty email reuses its client; a blank email never does.
3. A repeat or concurrent conversion creates no duplicates and adds no audit row or
   new conversion timestamp.
4. Dismissed and unknown inquiries fail without writes; read-only owners and guests
   cannot convert. Read-only owners retain detail read access.
5. Created client notes retain the original inquiry context; no proposal or external
   delivery occurs.
6. The iOS mutation validates the response and invalidates exactly the four affected
   snapshots only after success. Failure and malformed success preserve all snapshots.
7. Converted IDs open the correct web-admin records on the current workspace origin.
8. Backend tests use the public HTTP routes, including a concurrent-request case;
   Swift tests cover the exact endpoint, response validation, and cache behavior.

## Verification and rollback

Run focused mobile API tests, complete Python unit and non-unit partitions, Ruff,
and the existing macOS XCTest workflow. Use disposable databases only. Rollback is a
source revert; this slice introduces no migration. Records created in disposable
verification are discarded with their databases. Any real conversion remains subject
to the existing browser undo semantics, which never cascade-delete records.

## Post-implementation review

**Verdict:** Pragmatic partial — no demonstrated P1/P2/P3 source finding in the
single-pass author self-review. This is not an independent cross-seat review.

Reviewed the API and iOS diffs against every acceptance criterion and the existing
browser semantics. The implementation preserves the intended client/project shapes,
serializes duplicate attempts, refuses blank-email matches and dismissed leads,
returns the documented status/error codes, creates no proposal or external delivery,
and invalidates exactly the affected snapshots. Navigation is explicitly labeled as
opening the web admin rather than claiming native detail pages.

Review-driven test refinements assigned asynchronous cache reads before XCTest
assertions and covered both invalid conversion responses: an unconverted payload and
a converted payload missing its client ID.

Evidence at the final reviewed tree:

- focused mobile API suite: **63 passed**;
- full unit partition: **748 passed, 1 documented host-condition skip**;
- full non-unit partition: **385 passed**;
- Ruff: **402 files clean**;
- `git diff --check`: clean.

Residual verification: the new Swift conversion tests are written but not executed
on this Linux host. Run the existing macOS workflow for the conversion branch and
obtain independent non-author review before merge. No production or live-data check
was performed or claimed.
