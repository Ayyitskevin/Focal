# Inquiry owner-journey specification

**Status:** Phase 1 merged in #228. The converted client record shipped in #229. The second Phase 2 slice opens the converted project.
**Updated:** 2026-11-19
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
the actions that open converted records.

## Phase 1 — manual email reply

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
only an opaque generation identifier after completion and keep existing pending
drafts readable. Signing out or ending the session clears its private reply
text, including when a gallery request discovers the session has expired.
A temporary network or server failure preserves the draft.
Signing into the same studio from a second window can leave the first session
active. Each window keeps its original reply key until its session actually ends.
The owner can reopen to see current state. These guarantees apply within the
existing authenticated session.

### Acceptance evidence

Cover successful send, exact replay, key conflict, provider exception/unknown,
post-provider commit failure, missing configuration/recipient, invalid input,
read-only/guest refusal, and concurrent identical commands. Assert one provider call,
one outbound row, one emails_log row, one reply-state transition, and no sensitive
receipt content. Swift tests cover exact request encoding, sent/unknown decoding,
cache invalidation, retry after a lost response, competing composers, confirmed
delivery despite a local cleanup error, and removal of private draft text when
the session ends. Include replay after inquiry deletion
and a transient database connection failure after provider acceptance. Run full
Python and macOS gates with provider-faked sends only.

## Phase 2 — native records and compressed navigation

Add native client/project detail with the existing owner read scope, then change
converted-record links from web admin to those native pages. Simplify iPhone tabs to
**Home, Inquiries, Calendar, More**, keeping Clients, Projects, Galleries, and
Commercial under More. Preserve capability-aware client navigation and accessibility.
Resolve current native navigation correctness issues before adding more surfaces.

### First slice — open the converted client

An owner who converts an inquiry can tap **Open client record**, read that client
inside Focal and use Back to return to the same inquiry and its converted state.
This works for both a newly created client and an existing client linked by
conversion. The destination uses the returned `converted_client_id`. A linked
client can have a different name and contact details from the original inquiry.
At the end of this first slice, the project action still opened web admin.

The app reads `GET /api/v1/clients/{client_id}` with its existing owner session.
The response uses the same `ClientSummary` fields as the owner client directory.
The screen shows the client's name, available contact details and project count.
An owner with `studio:read` can open the record. Missing, guest and revoked
credentials receive the existing authorization errors, including conditional
requests. A valid owner login restored from an older app version can also read
the record. Refreshing that login preserves access, while replacing it or signing
out during a request discards the late response. A missing client has a visible
unavailable state. On a failed read,
the owner can retry or go back. Load by the exact ID and surface a mismatched
response as an error. Keep private record details out of persistent client-side
storage for this route.

Acceptance requires an iPhone simulator UI test through workspace sign-in,
conversion, native detail and Back with synthetic data on an isolated service.
Assert a client-only value on the detail screen and the identity of the original
inquiry after Back. Cover the authorization, missing-record and identity cases
at the API or repository boundary. Keep project detail and tab reorganization
for separate changes.

### Second slice — open the converted project

From a converted inquiry, **Open project record** pushes a read-only project screen
when the inquiry has a `converted_project_id`. Back returns to that same inquiry
with its converted state visible. A conversion that only created or linked a
client continues to show just its client action.

The screen reads `GET /api/v1/projects/{project_id}` using the signed-in owner’s
existing `studio:read` access. It shows the stored project title, linked client
name, and status. Show the shoot date when present. The API returns the directory’s
`ProjectSummary` shape for the requested ID and reports a missing project as 404.
Conditional requests run the same owner access checks. The screen handles an
unavailable record or failed read with Retry and Back. A different project ID or
a response for an ended or replaced session cannot populate the screen. Project
detail stays in memory.

On an iPhone simulator, sign in to a synthetic studio, convert a dated booking
inquiry, then open its project. Distinguish the stored project from another
project for the same client. Check the title, client, status, and shoot date on
screen, then use Back to verify the original inquiry and its converted actions.
On a second visit, simulate one failed read. Verify Retry opens the same project
and Back still returns to that inquiry. Keep the converted-client journey covered.

## Phase 3 — notifications and exact deep links

After replies work, add a device registry and notification outbox for new inquiries
and reply outcomes. Deep links open the exact authorized inquiry or converted record.
No notification may expose contact/message content without revalidating authority.
This phase requires its own schema, APNs, privacy, and production-readiness review.

## Phase 4 — semantic consolidation and existing correctness

### Convert an inquiry once across browser and iPhone

An owner can have the same inquiry open in web admin and on an iPhone. Whichever
**Convert** request reaches the studio first determines the client and project.
A retry, including one submitted while the first request is still running,
returns those same stored IDs. The inquiry keeps its first conversion timestamp.

The browser’s **Convert to Client** action and the iPhone’s conversion endpoint
share the [existing atomic conversion rules](INQUIRY-MOBILE-CONVERSION.md#api-contract).
The transaction checks the conversion stamp before creating records, links a
client by trimmed nonempty email, creates a project for a dated booking, and
writes one conversion audit entry. A failure rolls back the entire operation.
An empty or whitespace-only email supplies no identity: two such contacts each
get their own client, even when another client already has a blank email.
Contact inquiries create no projects.

The browser preserves its 303 redirect to the original project or client and
honors its existing local admin return destination. If linked records have
since been deleted, a retry keeps the surviving links. When both links are gone,
the browser returns 410 and explains that the records are unavailable. The
iPhone keeps its current owner-write requirement and returns
the converted inquiry from the same transaction. Quote creation and explicit
Undo retain their own workflows.

**Acceptance.** Submit the browser and iPhone requests in both orders, retry each
surface, and overlap their first requests for a fresh booking. Compare both
responses with the stored client ID, project ID, conversion stamp and audit count.
Confirm that two blank-email contacts remain distinct through retries and create
zero projects. In the iPhone simulator, race the actual Convert action with a
signed-in browser request. Simulate losing the first successful phone response,
retry from the phone, then open both linked records and return to the inquiry.

### Keep the booking and its inquiry linked to the same records

Booking confirmation creates an inquiry for the studio inbox. If an owner
converts that inquiry while confirmation is still running, the booking, the
inquiry, and the owner’s response must all identify the same client and project.
The first committed pair is authoritative. Retrying either identity operation
must reuse that pair and the original inquiry. Existing unrelated projects for
the same client are never candidates for the booking.

For a shoot, confirmation creates one project with the shoot date and booking
intake. For a consultation, confirmation links only the client and leaves the
inquiry available for manual conversion. If an owner explicitly promotes it,
the booking must reflect the project chosen by that conversion. A rescheduled
booking inherits established links from its source. Reusing links preserves
existing record edits and the first conversion stamp.

If the owner promotes a consultation after rescheduling it, the next conversion
or confirmation of any booking in that appointment's reschedule history reuses
the first project. A separate appointment for the same client keeps its own
project. Reusing the project leaves earlier inquiries' conversion stamps intact.

SQLite must serialize the choice of identity with the writes that publish it.
Readers and retries see either the committed links or the prior state after a
rollback. Email, calendar and Notion calls stay outside that transaction.
This change covers future confirmation and conversion attempts. Repairing old
duplicate projects or changing how often notifications are delivered is separate.

**Acceptance.** On disposable studio data, overlap confirmation and the owner's
conversion on the same booking in both orders. Compare the first result, the
booking and inquiry rows, and the owner's retry. Verify one linked client and
one project for the shoot, stable IDs on replay, consultation promotion and
reschedule inheritance. Keep external providers disabled or stubbed.

Separately fix existing gallery correctness issues (large-gallery
pagination, owner drafts, video playback) without expanding gallery scope. Reconcile
closed issues and retain independent review and macOS CI for every slice.

## Deferred

New AI capabilities, native payments/e-signatures, advanced gallery expansion, more
dashboard panels, broad hosted launch activity, and performance rewrites without a
measured baseline. SMS follows only after the email delivery contract proves itself.
