# Inquiry owner-journey specification

**Status:** Phase 1 active implementation; later phases specified, not started
**Date:** 2026-11-18
**Depends on:** merged inquiry detail/triage (#225) and conversion (#226)

## Product decision

Finish one complete owner journey before expanding Focal again:
**inquiry → reply → convert → open the record → take the next action.**
Use small independently reviewed slices. Do not add AI capabilities, native payment
or signature flows, advanced gallery features, or a broad multi-tenant launch while
this journey remains incomplete.

## Phase 0 — merged foundation and owner validation

Inquiry detail, conversation history, dismiss/restore, and atomic client/project
conversion are merged. Before public release, run a real-device or simulator smoke
pass with synthetic data covering navigation, conversion, repeat idempotency, and
web-admin links. This validation is not claimed by repository tests alone.

## Phase 1 — manual email reply (this implementation)

### Goal and non-goals

Let a write-scoped owner send one deliberate email reply from native inquiry detail.
No SMS, automatic sends, scheduled retries, provider credentials in tests, server-side
drafts, migration, or claim that SMTP acceptance proves inbox delivery.

### HTTP contract

`POST /api/v1/inquiries/{inquiry_id}/reply` requires exact owner + `studio:write` and
a UUID `Idempotency-Key`. JSON contains only `channel` (`email`), `subject`, and
`body`. Subject is required and bounded to 200 characters; body is required and
bounded to 100,000 characters. Unknown fields are rejected. The inquiry must exist,
have a non-empty email, and SMTP must be configured. Unknown inquiries return
`404 inquiry.not_found`; guests/read-only owners are refused without writes.

Before provider I/O, the server creates a session-bound replay receipt with a stable
request hash and `202 inquiry.delivery_unknown`. It also derives a stable RFC 5322
Message-ID from the session/inquiry/key so the same attempt has one identity. Receipts
contain hashes and bounded status metadata only—never the raw key, recipient, subject,
or body. Receipts expire with the authenticated session.

Outcomes:

- **201 `inquiry.reply_sent`** only after SMTP returns successfully and the outbound
  message, `emails_log` row, inquiry reply state, audit row, and sent replay receipt
  commit in one transaction. The response contains `InquiryDetail` and the new
  outbound message's integer database ID.
- **202 `inquiry.delivery_unknown`** when the receipt is pending or provider/result
  completion is ambiguous. The response contains the current detail and an unknown
  status, never a fabricated message ID. An ambiguous attempt is not automatically
  retried, preventing duplicate delivery after a timeout or post-SMTP failure.
- The same key with different canonical request content returns
  `409 idempotency.key_conflict` without another send. An exact replay returns the
  stored status/outcome without provider I/O.

If SMTP raises after being called, the receipt remains unknown and no second send is
attempted. If it succeeds but the database commit fails, the pending receipt likewise
remains unknown. Only successful completion creates a visible outbound thread row;
pending/unknown attempts do not appear as sent conversations or set `is_replied`.

### iOS behavior

- Inquiry detail offers **Reply by email** for write-scoped owners with a reachable
  inquiry email. The composer has a prefilled editable subject and required body.
- Sending requires explicit confirmation, disables duplicate taps, and uses one
  persisted session-scoped idempotency key for the exact draft retry.
- `201` replaces the detail, clears the draft, and refreshes inquiry/dashboard data.
- `202` keeps the draft and shows **Delivery unknown—check before sending again**.
- Other failures preserve the draft and the last confirmed detail. No automatic retry
  or offline-send promise is made. Stale responses cannot overwrite a newer detail.

### Retrying an unresolved native reply

An owner who receives an unknown result, loses the response, or reopens a saved
attempt sees a warning that the original email may already have gone out. The
composer loads the saved attempt before enabling a send. An exact retry in the
same session uses the saved key and the same trimmed subject and body. If the
first request never reached Focal, this retry can send the email for the first time.
An edited reply is a new email. Before replacing an unresolved attempt, show a
distinct confirmation explaining that the customer could receive both emails.
Cancelling preserves the saved attempt and sends nothing.

Draft preparation must return only the text the owner confirmed. Concurrent
identical first attempts share one key. A different first attempt that loses a
race, or an edit based on a saved attempt which has since changed or disappeared,
must stop before sending. This includes a composer that opened on an empty
draft before another composer sent its reply. Remember the completed attempt
locally so that only a newly opened composer can start the next email. Retain
only the attempt identifier after completion and keep existing pending drafts
readable. Signing out or ending the session clears its private reply text.
The owner can reopen to see current state. These guarantees apply within the
existing authenticated session.

### Acceptance evidence

Cover successful send, exact replay, key conflict, provider exception/unknown,
post-provider commit failure, missing configuration/recipient, invalid input,
read-only/guest refusal, and concurrent identical commands. Assert one provider call,
one outbound row, one emails_log row, one reply-state transition, and no sensitive
receipt content. Swift tests cover exact request encoding, sent/unknown decoding,
cache invalidation, retry after a lost response, competing composers, and removal
of private draft text when the session ends. Include replay after inquiry deletion
and a transient database connection failure after provider acceptance. Run full
Python and macOS gates with provider-faked sends only.

## Phase 2 — native records and compressed navigation

Add native client/project detail with the existing owner read scope, then change
converted-record links from web admin to those native pages. Simplify iPhone tabs to
**Home, Inquiries, Calendar, More**, keeping Clients, Projects, Galleries, and
Commercial under More. Preserve capability-aware client navigation and accessibility.
Resolve current native navigation correctness issues before adding more surfaces.

## Phase 3 — notifications and exact deep links

After replies work, add a device registry and notification outbox for new inquiries
and reply outcomes. Deep links open the exact authorized inquiry or converted record.
No notification may expose contact/message content without revalidating authority.
This phase requires its own schema, APNs, privacy, and production-readiness review.

## Phase 4 — semantic consolidation and existing correctness

Unify browser and native inquiry-conversion semantics in one small domain-service
change with regression tests, including blank-email handling and duplicate-project
prevention. Separately fix existing gallery correctness issues (large-gallery
pagination, owner drafts, video playback) without expanding gallery scope. Reconcile
closed issues and retain independent review and macOS CI for every slice.

## Deferred

New AI capabilities, native payments/e-signatures, advanced gallery expansion, more
dashboard panels, broad hosted launch activity, and performance rewrites without a
measured baseline. SMS follows only after the email delivery contract proves itself.
