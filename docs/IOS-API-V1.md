# Mise mobile API v1 contract

This document is the contract targeted by the native app. Milestone 1 implements
tenant discovery, every authentication/capability route below, session management,
and the scoped OpenAPI document. Milestone 2 adds the owner dashboard, client and
project collections, gallery manifests, event types, and booking agenda.
Milestone 3 (ADR 0067) adds the shared-client reads (`/client/home`,
`/client/galleries`, `/client/bookings`, project document collections), the
gallery-guest favorite toggle, and bearer-authenticated media routes. Milestone
4a adds owner task completion, booking cancellation, and the session-bound
booking-reschedule command below. S6e adds its durable, operator-visible delivery
workflow. S6f implements the source-aware slot read required by native
reschedule. Other commands remain planned.

## Conventions

- Base URL: the canonical tenant origin, e.g. `https://studio.example.com/api/v1`.
- Tenant selection: request host only. Never switch tenants from a body or header.
- Content types: `application/json` and `application/problem+json`.
- Authentication: `Authorization: Bearer <opaque-access-token>`.
- Timestamps: RFC 3339 UTC (`2026-07-09T22:30:00Z`).
- Date-only values: `YYYY-MM-DD`.
- Money: integer `minor_units` plus ISO 4217 `currency_code`. Mise's existing
  `*_cents` columns map to minor units without floating-point conversion.
- Collection pagination: opaque `cursor` and a bounded `limit`.
- Writes: accept `Idempotency-Key` where retry is safe or consequential.
- Cache validation: `ETag` responses and `If-None-Match` requests.
- Request correlation: server returns `X-Request-ID` and includes it in problems.

## Error shape

    HTTP/1.1 409 Conflict
    Content-Type: application/problem+json
    Retry-After: 3

    {
      "type": "https://mise.example/problems/proofing-limit",
      "title": "Proofing limit reached",
      "status": 409,
      "code": "gallery.proofing_limit",
      "detail": "This section already has 20 selections.",
      "request_id": "req_01J...",
      "errors": [
        {
          "path": ["asset_id"],
          "message": "Selection would exceed the section target.",
          "code": "proofing_limit"
        }
      ]
    }

Use 401 for absent/expired credentials, 403 for a known principal without the
required scope, and 404 when revealing resource existence would cross a scope.
Use 402 for unavailable tenant subscription state, 410 for expired public
capabilities, 409 for state/version/idempotency conflicts, and 429 with
`Retry-After` for throttling.

## Tenant/bootstrap

### `GET /api/v1/tenant`

Public, non-enumerating descriptor on the tenant host:

    {
      "cache_namespace": "tenant_42",
      "slug": "north-star-photo",
      "studio_name": "North Star Photo",
      "canonical_base_url": "https://north-star.mise.example",
      "brand_accent_hex": "#2F5C45",
      "time_zone": "America/New_York",
      "currency_code": "USD",
      "auth_methods": ["studio_password", "shared_access"]
    }

Do not return tenant filesystem paths, plan internals, owner email, or tenant ID
that a root endpoint could use to select a database.

## Authentication

### `POST /api/v1/auth/studio/login`

    {
      "email": "optional-owner@example.com",
      "password": "<redacted>",
      "device": {
        "installation_id": "0A90...-...",
        "name": "Kevin's iPhone",
        "platform": "ios",
        "app_version": "1.0 (42)"
      }
    }

Success:

    {
      "access_token": "<opaque>",
      "refresh_token": "<opaque>",
      "token_type": "Bearer",
      "access_token_expires_at": "2026-07-09T22:45:00Z",
      "refresh_token_expires_at": "2026-08-08T22:30:00Z",
      "workspace": {
        "cache_namespace": "tenant_42",
        "slug": "north-star-photo",
        "display_name": "North Star Photo",
        "api_base_url": "https://north-star.mise.example",
        "brand_accent_hex": "#2F5C45",
        "time_zone": "America/New_York",
        "currency_code": "USD"
      },
      "principal": {
        "id": "studio_owner",
        "kind": "studio_owner",
        "display_name": "North Star Photo",
        "email": "owner@example.com",
        "scopes": ["studio:read", "studio:write"]
      },
      "available_commands": ["booking.reschedule"]
    }

The same generic 401 problem covers unknown email and wrong password. Reuse the
current IP lockout and tenant password verifier.

`available_commands` is also returned by `GET /api/v1/me`. It contains
`booking.reschedule` only for a write-scoped studio owner while the default-off
durable workflow and outbound mail are configured. The native client must hide
reschedule otherwise; the command independently rechecks the gate.

### Other auth routes

| Method/path | Purpose |
| --- | --- |
| `POST /api/v1/auth/refresh` | rotate refresh token; atomic reuse detection |
| `POST /api/v1/auth/logout` | revoke current session/token family |
| `GET /api/v1/me` | return current principal, workspace, session metadata |
| `GET /api/v1/auth/sessions` | owner device/session list |
| `DELETE /api/v1/auth/sessions/{id}` | revoke another owner device |
| `POST /api/v1/client-auth/gallery/unlock` | gallery slug/PIN or link-only exchange |
| `POST /api/v1/client-auth/portal/unlock` | portal slug/PIN exchange |
| `POST /api/v1/client-auth/workspace/unlock` | workspace slug/PIN exchange |
| `POST /api/v1/client-auth/document/exchange` | one document capability exchange |

Each shared-access response uses an exact principal kind and narrow scope. A portal
exchange must not unlock its galleries; a workspace exchange must not become a
client-wide session.

## Initial read endpoints

| Method/path | Response |
| --- | --- |
| `GET /api/v1/dashboard` | `DashboardSummary` |
| `GET /api/v1/clients` | `Page<ClientSummary>` |
| `GET /api/v1/clients/{id}` | client detail |
| `GET /api/v1/projects` | `Page<ProjectSummary>` |
| `GET /api/v1/projects/{id}` | project detail and links |
| `GET /api/v1/galleries` | `Page<GallerySummary>` |
| `GET /api/v1/galleries/{id}` | `GalleryDetail` manifest |
| `GET /api/v1/projects/{id}/proposals` | `Page<Proposal>` |
| `GET /api/v1/projects/{id}/contracts` | `Page<Contract>` |
| `GET /api/v1/projects/{id}/invoices` | `Page<Invoice>` |
| `GET /api/v1/event-types` | `Page<EventType>` |
| `GET /api/v1/event-types/{id}/slots?day=YYYY-MM-DD` | server-computed available slots; optional validated `reschedule_booking_id` source exclusion (implemented, S6f) |
| `GET /api/v1/bookings` | `Page<Booking>` |
| `GET /api/v1/ai/runs` | `Page<AIRun>` |
| `GET /api/v1/galleries/{id}/cull` | paged cull deck/results |

The Milestone 2 endpoints are available only to the exact `studio_owner`
principal with `studio:read`. Client/project detail, AI-run, and
cull-result reads remain reserved contract surface until their delivery slices.

### Milestone 3 — shared-client reads (implemented)

| Method/path | Principals | Response |
| --- | --- | --- |
| `GET /api/v1/client/home` | any guest | capability-shaped `ClientHomeSummary` with server-computed `next_steps` |
| `GET /api/v1/client/galleries` | gallery/workspace/portal guest | `Page<GallerySummary>` scoped to that capability's galleries |
| `GET /api/v1/client/galleries/{id}` | gallery/workspace/portal guest | `GalleryDetail` manifest (404 outside scope) |
| `GET /api/v1/client/bookings` | workspace/portal guest | `Page<Booking>` for that capability's `client_id` only |
| `GET /api/v1/projects/{id}/proposals` | owner, or that exact workspace guest | `Page<Proposal>`; drafts never serialize |
| `GET /api/v1/projects/{id}/contracts` | owner, or that exact workspace guest | `Page<Contract>` |
| `GET /api/v1/projects/{id}/invoices` | owner, or that exact workspace guest | `Page<Invoice>` with payments and derived balance |

Document DTOs carry a `public_url` (`/p /c /i`) because accept/decline,
signing, and checkout remain canonical web flows until their own milestones.
A gallery exchange cannot read documents; a workspace exchange cannot open a
sibling project; a document exchange cannot widen into galleries or bookings.

Collections default to 25 and cap at 100. Cursors carry ordering state but no
authorization; authorization is reevaluated on every page.

## Initial commands

| Method/path | Semantics |
| --- | --- |
| `PUT /api/v1/galleries/{g}/assets/{a}/favorite` | idempotently select (implemented, Milestone 3) |
| `DELETE /api/v1/galleries/{g}/assets/{a}/favorite` | idempotently unselect (implemented, Milestone 3) |
| `PUT /api/v1/tasks/{id}/completion` | owner: idempotently check a task off; audited (implemented, M4a) |
| `DELETE /api/v1/tasks/{id}/completion` | owner: idempotently reopen a task; audited (implemented, M4a) |
| `POST /api/v1/galleries/{g}/assets/{a}/comments` | add video comment/reply |
| `POST /api/v1/proposals/{id}/accept` | server-authoritative transition |
| `POST /api/v1/proposals/{id}/decline` | server-authoritative transition |
| `POST /api/v1/contracts/{id}/sign` | hash/version checked signature evidence |
| `POST /api/v1/invoices/{id}/checkout` | return server-created hosted checkout URL |
| `POST /api/v1/bookings` | atomically revalidate slot and create booking |
| `POST /api/v1/bookings/{id}/cancel` | owner: idempotently cancel a confirmed booking; audited; fires the client cancel notice (implemented, M4a) |
| `POST /api/v1/bookings/{id}/reschedule` | owner: atomically create replacement/cancel old and enqueue durable delivery; audited; session-bound replay |
| `GET /api/v1/booking-workflows/{workflow_id}` | owner: bounded delivery status, with no booking contact/body/token fields |
| `POST /api/v1/booking-workflows/{workflow_id}/retry` | write-scoped owner: reset only blocked effects and wake delivery |
| `PATCH /api/v1/galleries/{g}/assets/{a}/cull` | owner keep/cut/restore command |
| `POST /api/v1/captions/{id}/draft` | explicit AI draft; never auto-approve |

Contract signing, checkout, booking, rescheduling, and AI commands require an
`Idempotency-Key`. The server retains transition rules, amount math, hash checks,
Stripe reconciliation, audit logging, and workflow dispatch.

Task check-off (M4a) needs no `Idempotency-Key`: like the favorite toggle it models
completion as an idempotent sub-resource (`PUT` = ensure done, `DELETE` = ensure
open), so a repeat call is a safe no-op returning current state. It requires an owner
bearer carrying `studio:write` and writes one `audit_log` row per real transition
(`task`/`complete`|`reopen`, actor `owner`) — the web `/admin/tasks/{id}/toggle` path
writes none. Booking cancellation is naturally idempotent; when it targets a
replacement whose provider effect is already running, it returns
`409 booking.workflow_in_progress` without changing either state. Rescheduling
is not idempotent — each successful command creates a new row — so it uses the
replay contract below.

### Owner booking availability

`GET /api/v1/event-types/{event_type_id}/slots?day=YYYY-MM-DD` returns
server-computed availability for one business-local day. Native reschedule must
also send `reschedule_booking_id={source_booking_id}`; the server verifies that
the source exists, is confirmed, and uses the route event type before excluding
it from overlap and same-day daily-cap accounting.

    {
      "event_type_id": 7,
      "day": "2026-07-16",
      "time_zone": "America/New_York",
      "reschedule_booking_id": 41,
      "slots": [
        {
          "start_at": "2026-07-16T13:00:00Z",
          "end_at": "2026-07-16T14:00:00Z"
        }
      ]
    }

Slots are sorted, unique UTC whole-second instants. `day` and `time_zone` use
the server business zone; the reschedule command still sends the source
booking's `time_zone` as its client-facing display context. The source's current
start is omitted because the command rejects an unchanged booking. Empty
availability is `200` with `slots: []`.

This response is advisory and never reserves a slot. When a source booking owns
a mirrored Google event, the preview excludes that event only after an
identity-aware event list reconciles with canonical Google free/busy. If the
identity query fails or does not reconcile, the full free/busy result is kept and
the preview may be more restrictive than the transactional booking rule. The
eventual `POST /reschedule` always re-derives database availability while holding
its writer lock, so stale or forged submissions still return
`409 booking.slot_unavailable`. Responses use `Cache-Control: no-store`.
Unknown event types or source bookings are `404`. Stable source-state conflicts
are `409 booking.event_unavailable`, `booking.not_reschedulable`, or
`booking.event_mismatch`.

### Owner booking reschedule

`POST /api/v1/bookings/{booking_id}/reschedule` requires an exact
`studio_owner` bearer with `studio:write` plus a UUID `Idempotency-Key` header.

    {
      "start_at": "2026-07-16T11:00:00Z",
      "time_zone": "America/New_York"
    }

`start_at` is an aware RFC 3339 whole-second instant; equivalent offsets and a
zero fractional component canonicalize to UTC. `time_zone` is a valid IANA name
used for the replacement booking's client-facing display context. The destination
still comes from business-local availability and is revalidated server-side.

    {
      "status": "rescheduled",
      "workflow_id": "86aa7d32-740e-4993-af8d-438bb80c366b",
      "delivery_status": "pending",
      "original_booking_id": 41,
      "replacement_booking_id": 42,
      "start_at": "2026-07-16T11:00:00Z",
      "end_at": "2026-07-16T12:00:00Z"
    }

After a SQLite `BEGIN IMMEDIATE` writer lock, the command rechecks the mobile
session, source state, event type, availability, notice/window policy, overlaps,
and daily cap. Replacement creation, source cancellation, linkage/intake and
external-ID transfer, inquiry/project date reconciliation, two audit rows, six
workflow-effect rows, and the exact minimal replay response commit together.
Failure to enqueue rolls the entire command back. No bearer, raw idempotency key,
booking manage token, client contact field, or rendered message is copied into the
receipt, audit payload, or workflow table.

Replay scope is deliberately `(session_id, hash(Idempotency-Key))`. A refreshed
access token in the same device session gets the exact stored response; the same
key with a different canonical request returns `409 idempotency.key_conflict`.
A logout/new login is a new replay namespace. Receipts become unusable at the
session's absolute expiry and the recurring scheduler physically prunes them.
This session scope is approved for booking reschedule only; payment/signature
commands need their own durable-identity decision before reusing it.

State conflicts return stable `409` codes: `booking.not_reschedulable`,
`booking.unchanged`, `booking.event_unavailable`, `booking.slot_unavailable`, or
`booking.workflow_in_progress` while a prior replacement effect is already
running.
Unknown bookings are `404`; malformed body/header input is `422`. If durable
delivery is not armed, the command returns
`503 booking.reschedule_unavailable` before any booking write.

A `200` means the booking transition and workflow enqueue committed; it is not
proof of provider delivery. The immutable replay therefore keeps
`delivery_status: "pending"` and returns the stable `workflow_id` for status
reads. After commit, an immediate dispatch wake is attempted; a startup drain and
short periodic sweep recover crashes and expired leases in every retained tenant
database, including non-billable tenants. Hosted recovery re-resolves immutable
tenant identity and opens SQLite existing-only, so missing/deleted DBs and delete
races cannot silently recreate storage.

The six ordered effects are:

1. old client invite: `METHOD:CANCEL`, old UID/times,
   `STATUS:CANCELLED`, `SEQUENCE:1`;
2. replacement invite: `METHOD:REQUEST`, replacement UID/times/manage URL,
   `STATUS:CONFIRMED`, `SEQUENCE:0` — eligible only after effect 1 succeeds or
   is explicitly inapplicable;
3. one studio reschedule notice;
4. patch/reuse the existing Notion booking page;
5. patch/reuse the shared Notion Session;
6. move/reuse the Google Calendar event.

Each effect has its own attempt count, due time, lease, terminal status, provider
reference, and bounded error class/code. Completed/skipped effects never rerun.
Blocked CANCEL holds REQUEST pending; manual retry resets blocked effects only.
Google/Notion identifiers move to the replacement (the shared Session remains on
both historical rows), preventing duplicate external objects.

Migration 083 stores calendar identity before changing its generation rule:
preexisting bookings keep the exact `mise-booking-{id}@kleephotography.com` UID
already issued to clients, while new bookings use a tenant-scoped UID. Cancelling
or rescheduling a replacement under any mobile, public, or admin lifecycle path
atomically marks its non-CANCEL queued effects `skipped` with
`replacement_superseded`; the original source CANCEL remains eligible. An expired
non-CANCEL lease is retired under the lifecycle writer lock even if delivery is
currently disarmed. A non-CANCEL effect with an active lease returns 409 rather
than racing provider I/O.

SMTP cannot provide transactional exactly-once acceptance. Delivery is honest
at-least-once: stable Message-IDs reduce duplicates, but a crash after provider
acceptance and before the local success stamp can resend the same logical message.

### Booking workflow status and retry

`GET /api/v1/booking-workflows/{workflow_id}` requires a studio owner and
returns:

    {
      "workflow_id": "86aa7d32-740e-4993-af8d-438bb80c366b",
      "status": "retry",
      "source_booking_id": 41,
      "replacement_booking_id": 42,
      "effects": [
        {
          "kind": "client_cancel_ics",
          "sequence": 10,
          "status": "retry",
          "attempts": 2,
          "next_attempt_at": "2026-07-13T18:30:05Z",
          "completed_at": null,
          "provider_ref": null,
          "error_class": "TimeoutError",
          "error_code": "exception"
        }
      ]
    }

Overall state is `pending|running|retry|succeeded|blocked`; effect state is
`pending|running|retry|succeeded|skipped|blocked`. The response contains only
stable IDs and bounded operational evidence—never contact fields, message bodies,
credentials, raw tokens, or manage URLs.

`POST /api/v1/booking-workflows/{workflow_id}/retry` additionally requires
`studio:write`. It returns `409 booking.workflow_not_retryable` when nothing is
blocked, `404 booking.workflow_not_found` for an unknown tenant-local workflow,
and `503 booking.reschedule_unavailable` while dispatch is disarmed. The endpoint
revalidates the session after acquiring its writer lock, resets blocked effects,
and appends bounded owner/session audit evidence in the same transaction; only
after commit does it wake delivery.

This S6e contract is scoped to the owner `/api/v1` command. The existing public
browser reschedule path remains on its legacy notification flow and must not be
described as having these durable CANCEL/REQUEST guarantees. It does share the
canonical atomic supersession/busy guard, so it cannot resurrect a queued native
replacement workflow.

## Owner commercial spine (implemented — read-only)

Native mirror of the operator's F&B commercial surfaces (ADRs 0039–0046): the
company next-action ranking, the studio commercial action queue, AR chase
assist + cadence, and project closeout readiness. Every route is **owner-only
(`studio_owner` + `studio:read`) and purely read**. Implemented in
`app/mobile_commercial_api.py` (queue S8), reusing the extracted admin
derivations — `_ctx_commercial_actions`, `_ranked_company_actions`,
`_company_next_actions`, `_ar_chase_context`/`_company_overdue_rows`/
`_ar_chase_history`, and `_project_closeout` in `app/commercial.py` (S7) —
behind DTOs, per backend note 6
(share the query function, do not re-derive or HTTP-call the HTML route). No new
authority is introduced and no value is auto-sent, charged, or mutated.

**A "company" is not a table.** It is a **root client** — a `clients` row with
`parent_id IS NULL` — standing for the whole descendant group
(`clients.descendant_ids`). `{company_id}` in these routes is that root client's
id; a non-root or unknown id is `404`. Display name is `company or name`. The
existing `GET /api/v1/clients` still lists every client, flat; `/companies`
lists only the roots.

| Method/path | Response |
| --- | --- |
| `GET /api/v1/companies` | `Page<CompanySummary>` (root clients) |
| `GET /api/v1/commercial/actions` | `Page<CommercialAction>` (queue: top action per company) |
| `GET /api/v1/companies/{id}/next-actions` | `CompanyNextActions` (ranked ≤6 for one company) |
| `GET /api/v1/companies/{id}/ar-chase` | `ArChaseAssist` (optional `?invoice_id=` narrows to one) |
| `GET /api/v1/projects/{id}/closeout` | `ProjectCloseout` |

### Structured targets, not admin links

Each admin action/checklist row carries an `href` into an HTML admin page (often
a page anchor like `#shot-list`). The native contract never exposes those.
Instead every actionable row carries a typed `target` the app resolves through
its own authorization-aware router:

    "target": { "kind": "ar_chase", "company_id": 9, "project_id": null,
                "invoice_id": 4120, "section": null, "url": null }

`kind` ∈ `company | ar_chase | project | invoice | gallery | workspace | external`.
`section` (for `project`) hints the sub-panel ∈ `shots | deliverables | invoices |
license | ar | details`. `url` is set only for `external`/public destinations
(e.g. a live workspace `/w/{slug}`), never an admin path. The server owns the
one authoritative `href → target` mapping.

### `CommercialAction` / `NextAction`

`priority` is the admin `rank` (lower = more urgent); `severity` maps the admin
`tone` (`warn → attention`, `gap → missing`). `detail` is the admin `label`.

    {
      "company_id": 9,
      "company_name": "Blue Plate Group",
      "priority": 10,
      "severity": "attention",
      "title": "Chase past-due invoice",
      "detail": "3 past due · $4,200 owed",
      "meta": "money",
      "target": { "kind": "ar_chase", "company_id": 9, "invoice_id": null,
                  "project_id": null, "section": null, "url": null }
    }

`GET /api/v1/commercial/actions` returns one row per company (the top-ranked
action), ordered `(priority, company_name, title)` — the queue caps at 8 like
`_ctx_commercial_actions`. `CompanyNextActions` returns the full ranked strip
(≤6) for a single company, dropping the `company_*` fields:

    {
      "company_id": 9,
      "company_name": "Blue Plate Group",
      "actions": [ { "priority": 10, "severity": "attention",
                     "title": "Chase past-due invoice", "detail": "3 past due · $4,200 owed",
                     "meta": "money", "target": { "kind": "ar_chase", "company_id": 9, ... } } ]
    }

### `ArChaseAssist`

Mirrors `_ar_chase_context`. Money is `Money{minor_units,currency_code}` from the
`*_cents` fields. Invoices carry a `public_url` (`/i/{slug}`), never the raw
slug. The `draft` is a **read-only preview** of the chase email — the actual
send is the one mutation on the admin side and is **not** in this read slice
(see below).

    {
      "company_id": 9,
      "company_name": "Blue Plate Group",
      "owed": { "minor_units": 420000, "currency_code": "USD" },
      "overdue_invoices": [
        {
          "invoice_id": 4120, "title": "November coverage", "status": "sent",
          "due_date": "2026-06-15",
          "total": { "minor_units": 250000, "currency_code": "USD" },
          "paid":  { "minor_units": 50000,  "currency_code": "USD" },
          "owed":  { "minor_units": 200000, "currency_code": "USD" },
          "project_id": 31, "project_title": "Q4 Menu Refresh",
          "client_id": 14, "client_name": "Blue Plate — Downtown",
          "public_url": "https://studio.example.com/i/inv-2026-118"
        }
      ],
      "cadence": {
        "status": "recent",
        "followup_due": false,
        "days_since": 2,
        "last_sent_at": "2026-07-10T14:02:00Z",
        "last_sent_to": "ap@blueplate.example",
        "next_due_on": "2026-07-17",
        "summary": "last chased 2d ago",
        "detail": "You emailed a balance reminder 2 days ago."
      },
      "draft": {
        "to": "ap@blueplate.example",
        "subject": "Follow-up on open invoice balance - Blue Plate Group",
        "body": "Hi Blue Plate Group, ..."
      }
    }

`cadence.status` ∈ `never | recent | due` (from `_ar_chase_history`);
`last_sent_at` is the `emails_log` timestamp in RFC 3339; `next_due_on` is the
date-only follow-up target (`MISE_AR_CHASE_FOLLOWUP_DAYS`, default 7).

### `ProjectCloseout`

Mirrors `_project_closeout`. `severity` maps `tone` (`ok → ok`, `warn →
attention`, `gap → missing`); `ready` is `attention == 0 and missing == 0`.

    {
      "project_id": 31,
      "ready": false,
      "ok_count": 4, "attention_count": 2, "missing_count": 1, "total": 7,
      "items": [
        {
          "key": "deliverables", "title": "Deliverables",
          "severity": "attention", "badge": "Needs attention",
          "detail": "5/8 delivered",
          "target": { "kind": "project", "project_id": 31, "section": "deliverables",
                      "company_id": null, "invoice_id": null, "url": null }
        }
      ]
    }

`key` ∈ `shots | deliverables | license | invoice | ar | gallery | workspace`.
A `gap` workspace row has `target: null` (nothing to open yet); an `ok`
workspace row targets the live `/w/{slug}` as `kind: "external"`.

### Out of scope for this read slice (deferred to Milestone 4)

The AR-chase **send** (`POST /admin/studio/companies/{id}/ar-chase/email`) is the
only mutation across all four surfaces. It sends the email and writes exactly one
`emails_log` audit row (`doc_kind='other'`, `doc_id=company_id`) — it never
touches invoices, payments, licenses, or any status. It is **not** part of this
read slice. When implemented natively it is an M4 command
(`POST /api/v1/companies/{id}/ar-chase/send`) requiring an `Idempotency-Key`,
preserving the same single-audit-row, no-financial-mutation contract, and it
stays a deliberate human action — consistent with the money/rights boundary and
§11.4. Until then the app previews the draft and hands off to the web to send.

### Cache + pagination

`/companies` and `/commercial/actions` are cursor collections (default 25, cap
100), authorization re-evaluated per page. The per-company and per-project detail
resources carry `ETag`/`Last-Modified` with `Cache-Control: private, no-cache`;
these are derived, fast-changing views, so treat a cached copy as a stale snapshot
with an age, never authoritative (offline policy already says the same for
dashboard/CRM summaries).

## Gallery manifest

The detail response deliberately nests list-card fields under `summary` and all
authorized media variants under `links`. This is the canonical wire shape:

    {
      "summary": {
        "id": 17,
        "title": "Amelia + Sam",
        "slug": "Q6Y...",
        "client_id": 9,
        "project_id": 12,
        "client_name": "Amelia + Sam",
        "type": "gallery",
        "published": true,
        "requires_pin": true,
        "content_revision": 42,
        "cover_asset_id": 201,
        "expires_on": "2026-10-01",
        "asset_count": 84,
        "favorite_count": 7,
        "download_count": 2,
        "delivery_state": "proofing",
        "created_at": "2026-07-01T12:00:00Z"
      },
      "sections": [
        {
          "id": 8,
          "gallery_id": 17,
          "name": "Ceremony",
          "caption": null,
          "position": 0,
          "proof_target": 20,
          "selected_count": 7
        }
      ],
      "assets": [
        {
          "id": 201,
          "gallery_id": 17,
          "section_id": 8,
          "kind": "photo",
          "status": "ready",
          "filename": "KLP_1024.jpg",
          "width": 6000,
          "height": 4000,
          "duration_seconds": null,
          "byte_count": 1842201,
          "position": 1,
          "created_at": "2026-07-01T12:01:00Z",
          "is_favorite": true,
          "favorite_count": 7,
          "links": {
            "thumbnail_url": "https://studio.example.com/api/v1/media/galleries/17/assets/201/thumbnail",
            "preview_url": "https://studio.example.com/api/v1/media/galleries/17/assets/201/preview",
            "poster_url": null,
            "download_url": "https://studio.example.com/api/v1/media/galleries/17/assets/201/download"
          },
          "alt_text": "A couple during their ceremony",
          "keywords": ["ceremony", "couple"],
          "keeper_score": 0.98,
          "hero_potential": 0.86,
          "cull_state": "keep"
        }
      ],
      "hero_asset_ids": [201],
      "vision": null
    }

Never expose gallery/portal PINs, `stored` filenames, or server paths. Media URLs
must enforce bearer scope, gallery publication/expiry, asset parentage/readiness,
and the cull delivery gate. Support Range requests for video and conditional/private
caching. Do not put bearer credentials in signed URL query parameters.

Milestone 3 implements the authenticated media routes and fills every manifest
link with an absolute URL on the request origin:

    GET /api/v1/media/galleries/{g}/assets/{a}/{thumbnail|preview|poster|download}

Authorization is re-derived per request: the owner (`studio:read`) and the
gallery's own guest see every variant; that guest needs the
`gallery:{g}:download` scope for originals; workspace/portal guests whose
capability covers the gallery get variants but never `download`. Published,
expiry, readiness, and cull delivery gates apply before any path resolution.
`FileResponse` provides Range support for video. Favoriting (gallery guest
only) is keyed to the session's minted server-side visitor and enforces the
per-section proofing cap with a `gallery.proofing_limit` 409 problem.

Media requests use a dedicated generous `api_media` rate bucket (a native
grid legitimately bursts one request per visible cell); `/download` variants
share the web `download` bucket.

## Offline/cache metadata

Successful detail/collection responses should include:

- `ETag` derived from a representation revision
- `Last-Modified` where meaningful
- `Cache-Control: private, no-cache` for manifests (store allowed, revalidate)
- `Cache-Control: private, max-age=...` plus ETag for immutable media variants

A 304 contains no body. Destructive changes need tombstones or a server sync feed
before `updated_since` can safely replace full collection reconciliation.

## Backend implementation notes

1. Add a dedicated router/dependency stack. Do not use `require_admin()` because it
   redirects to HTML.
2. Add API sessions/tokens and APNs devices through a schema/security PR under the
   repository rules.
3. Explicitly rate-limit `/api/v1/auth`, shared-access unlocks, media/downloads,
   reads, and mutations.
4. Permit only the required auth/billing/export routes through expired-tenant
   middleware and keep 402 JSON behavior.
5. Reject cookie-only authentication on protected API routes. Keep CSRF middleware
   unchanged for web routes.
6. Extract shared business/query functions instead of making internal HTTP calls
   from API routes to HTML routes.
7. Publish a scoped `/api/v1/openapi.json` and run schema/Swift fixture contract
   tests in CI even if public Swagger UI remains disabled.
8. Enter `tenant_runtime` for background notifications and scheduled work.
