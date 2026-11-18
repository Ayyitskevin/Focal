# Mobile inquiry detail and safe triage specification

**Status:** Implemented locally; macOS/XCTest and independent cross-seat review pending  
**Date:** 2026-11-17  
**Scope:** Focal owner companion, `/api/v1` inquiry resources, and the existing browser inquiry semantics.

## Review findings

The merged mobile inbox (`GET /api/v1/inquiries` and `InquiriesView`) is a useful
searchable list, but it ends at a 280-character preview. An owner cannot open the
full message, inspect the recorded conversation, or move a lead out of the active
pipeline without returning to the browser.

The browser inbox already has the required domain behavior:

- inquiries are open, dismissed, or converted;
- dismissing is reversible and retains the inquiry;
- converted inquiries cannot be dismissed;
- stored `messages` rows form the conversation, with the original inquiry message
  synthesized as one inbound message only when no stored rows exist;
- an outbound message, not the new-inquiry notification flag, means "replied."

The native API also has established seams to reuse: exact owner authentication,
`studio:write` mutation guards, transactional writes with audit rows, private ETag
reads, nested detail DTOs, and repository invalidation after successful mutations.

## Goal

Let a write-scoped owner open an inquiry on iOS, read its recorded conversation,
and dismiss or restore it with explicit, server-authoritative feedback. Keep the
browser and mobile surfaces on the same stored state without adding schema or
widening authority.

## Non-goals for this slice

- Quick quotes, client/project creation, or conversion undo.
- Email/SMS replies or any external delivery.
- New migrations, authentication changes, payment/invoice changes, or contract flows.
- APNs, universal links, and push notification delivery.
- A second message-pagination interface or a rewrite of the browser inbox.

Those are follow-up slices with separate money, record-creation, delivery, or UX
review requirements.

## API contract

### `GET /api/v1/inquiries/{inquiry_id}`

Requires the same exact `studio_owner` principal and `studio:read` scope as the
existing collection. An unknown ID returns `404 inquiry.not_found`.

The private, ETag-validated response contains:

- `summary`: the existing `InquirySummary` wire shape, unchanged;
- `messages`: up to the latest 200 recorded messages, returned chronologically;
- `has_older_messages`: `true` when older rows were omitted.

Each message contains its integer `id`, `direction` (`in` or `out`), bounded
`channel`, `body`, UTC `recorded_at`, and `is_truncated`. Bodies are capped at
65,536 characters; `is_truncated` makes any cap visible rather than pretending the
body is complete. When no stored message rows exist, message ID `0` represents the
original inquiry as one inbound message, matching the browser fallback. When rows
exist, the original inquiry is not duplicated.

### `PUT /api/v1/inquiries/{inquiry_id}/dismissal`

Ensures the inquiry is dismissed and returns the same detail shape. It requires
`studio:write`. A converted inquiry returns `409 inquiry.converted` and remains
unchanged. Repeating an already-dismissed request is a no-op: the timestamp and
audit history do not change.

### `DELETE /api/v1/inquiries/{inquiry_id}/dismissal`

Ensures the inquiry is not dismissed and returns the same detail shape. It requires
`studio:write`. Restoring an already-open inquiry is a no-op. If the inquiry is
converted, its converted status remains authoritative; this command never clears
conversion fields.

Both dismissal commands are naturally idempotent sub-resource writes, so neither
needs an `Idempotency-Key`. Every real transition and its `audit_log` row commit in
one transaction. Mutation responses are not cached. No command sends a message,
creates a client/project, or touches money state.

## iOS behavior

- Inquiry rows navigate to a detail screen instead of being static content.
- Detail is network-first and session-memory-only. The existing summary-list cache
  remains supported, but full conversation bodies are not added to persisted cache.
- The screen shows contact/service facts, status, reply state, and conversation
  bubbles with direction, channel, and recorded time. Truncation and omitted older
  history are labeled when present.
- Write-scoped owners see **Dismiss inquiry** for open inquiries and **Restore
  inquiry** for dismissed inquiries. Dismiss requires confirmation; restore does
  not. Converted inquiries expose no dismissal control.
- Controls are disabled while a mutation is running. Success uses the returned
  detail immediately, then refreshes the summary list. Failure preserves the last
  confirmed detail and explains that the server did not confirm the change.
- Read-only owners retain full read access with no mutation controls.

## Acceptance criteria

1. An owner can open any inquiry from the mobile list and read the same fallback or
   stored conversation semantics used by the browser inbox.
2. Guests cannot read inquiry details; read-only owners can read but cannot mutate.
3. Dismiss and restore persist across a fresh read and invalidate the inquiry-list
   and dashboard caches only after a valid server response.
4. Repeated commands return current state without new timestamps or audit rows.
5. Converted inquiries cannot be dismissed, and restore never clears conversion.
6. Unknown IDs and invalid path IDs produce bounded native API errors.
7. Long bodies are bounded and explicitly marked; older history omission is explicit.
8. Swift wire tests cover DTOs, exact endpoints, cache invalidation, and failure
   preservation. Backend tests exercise the public HTTP routes, not private helpers.

## Verification and rollout

Run focused mobile API tests first, then the complete Python unit and non-unit
partitions plus Ruff. Run the existing macOS XcodeGen/XCTest workflow for the Swift
change. This slice needs no migration or production data write. Rollback is a normal
revert of the API and iOS changes; existing inquiry rows remain valid and untouched
by the rollback itself.

## Later opportunities

Add open/booking/archived filters, message-history pagination, conversion flows,
reply composition with explicit delivery outcomes, and push/deep-link navigation.
Each should be selected and reviewed separately rather than absorbed into this slice.
