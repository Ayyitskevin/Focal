# Mise mobile API v1 contract

This document is the contract targeted by the native app. Milestone 1 implements
tenant discovery, every authentication/capability route below, session management,
and the scoped OpenAPI document. Milestone 2 adds the owner dashboard, client and
project collections, gallery manifests, event types, and booking agenda. Milestone 3
adds capability-bound gallery delivery plus portal, workspace, and document reads.
Milestone 4A adds owner client/project details and bounded client, project, and task
commands. Milestone 4B adds owner booking management and exact-capability proposal
decisions. Client booking creation, money commands, and native legal signing remain
planned. Milestone 5A adds the owner-only APNs device and notification contract.
Milestone 5B.1 adds the flag-gated native owner cull review, protected derivatives,
and reversible keep/cut/restore command. Milestone 5B.2 adds a privacy-bounded,
read-only owner AI activity ledger. Milestone 5B.3 adds native owner caption reads,
versioned draft editing, and default-off asynchronous immutable suggestions.

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
      "session_id": "<opaque-session-id>",
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
      }
    }

The same generic 401 problem covers unknown email and wrong password. Reuse the
current IP lockout and tenant password verifier.

### Other auth routes

| Method/path | Purpose |
| --- | --- |
| `POST /api/v1/auth/refresh` | rotate refresh token; atomic reuse detection |
| `POST /api/v1/auth/logout` | revoke current session/token family |
| `GET /api/v1/me` | return token-free current principal and workspace |
| `GET /api/v1/auth/sessions` | owner device/session list |
| `DELETE /api/v1/auth/sessions/{id}` | revoke another owner device |
| `POST /api/v1/client-auth/gallery/unlock` | gallery slug/PIN or link-only exchange |
| `POST /api/v1/client-auth/portal/unlock` | portal slug/PIN exchange |
| `POST /api/v1/client-auth/workspace/unlock` | workspace slug/PIN exchange |
| `POST /api/v1/client-auth/document/exchange` | one document capability exchange |

Each shared-access response uses an exact principal kind and narrow scope. A portal
exchange must not unlock its galleries; a workspace exchange must not become a
client-wide session.

## Owner notification device

These routes require the exact `studio_owner` principal with `studio:read`. They
derive tenant, session, principal, APNs topic, canonical HTTPS origin, and workspace
cache namespace from the authenticated request. Guest capabilities cannot register.

### `POST /api/v1/devices`

Register or rotate the current APNs token. APNs tokens are variable-length,
even-length hexadecimal strings; clients must not assume a 64-character token.

    {
      "installation_id": "a8a06dc2-2034-4e3b-b07d-0cbfd2455b98",
      "apns_token": "<lowercase-hex>",
      "environment": "sandbox",
      "locale": "en-US",
      "app_version": "1.0 (42)",
      "preferences": {
        "new_bookings": true,
        "booking_changes": true,
        "proposal_responses": true,
        "payments": true
      }
    }

`preferences` is optional and merges with an existing registration. Ordinary token
refreshes omit it so an app launch never resets user choices. The installation UUID
must match the hash bound during login; it is never returned and never appears in a
device route.

Every supplied preference value must be a JSON Boolean. Strings, integers, and
explicit `null` are rejected rather than coerced or silently treated as omitted.

Success includes `Cache-Control: no-store` and a strong `ETag`:

    {
      "environment": "sandbox",
      "locale": "en-US",
      "app_version": "1.0 (42)",
      "preferences": {
        "new_bookings": true,
        "booking_changes": true,
        "proposal_responses": true,
        "payments": true
      },
      "active": true,
      "registered_at": "2026-07-11T14:00:00Z",
      "updated_at": "2026-07-11T14:00:00Z"
    }

The response never includes the installation ID, database ID, session ID, APNs
token, keyed token hash, token version, ciphertext, topic, or tenant selector.

### Current-device routes

| Method/path | Semantics |
| --- | --- |
| `GET /api/v1/devices/current` | return current active registration plus `ETag` |
| `PATCH /api/v1/devices/current` | merge `{ "preferences": { ... } }`; requires `If-Match` |
| `DELETE /api/v1/devices/current` | idempotently erase encrypted token material; returns `204` |

The server uses an internal monotonically increasing revision when computing the
ETag, so two mutations within one SQLite timestamp second cannot recycle a stale
version. Logout, refresh-token replay, password rotation, explicit session
revocation, absolute expiry, and permanent APNs token rejection all deactivate the
bound registration and erase its ciphertext.

### APNs navigation envelope

Lock-screen copy is a fixed generic template. The only custom payload is this exact,
versioned navigation intent:

    {
      "mise": {
        "version": 1,
        "event_id": "018f632f-735d-7a16-8f31-2fb65d3f6e91",
        "workspace_origin": "https://north-star.mise.example",
        "workspace_cache_namespace": "workspace_a0d5...",
        "principal_kind": "studio_owner",
        "principal_id": "studio_owner",
        "route": "/app/bookings/41"
      }
    }

Initial emitted routes are `/app/projects/{positiveId}` and
`/app/bookings/{positiveId}`. `/app/home` is also part of the parser contract. A
payload contains no tenant ID, bearer/capability credential, client identifier,
name, contact detail, note, location, amount, or arbitrary URL. The app rechecks
the active session, scope, exact origin, cache namespace, principal, and typed route
before performing an ordinary authorized API fetch.

## Client delivery reads

Client delivery routes derive their resource exclusively from the bearer principal.
They accept no gallery, portal, project, document, tenant, or slug identifier as
authority:

| Method/path | Exact principal | Response |
| --- | --- | --- |
| `GET /api/v1/client/gallery` | `gallery_guest` | guest-safe `GalleryDetail` |
| `GET /api/v1/client/gallery/assets/{assetId}/thumbnail` | `gallery_guest` read | JPEG thumbnail |
| `GET /api/v1/client/gallery/assets/{assetId}/preview` | `gallery_guest` read | JPEG or Range-capable MP4 |
| `GET /api/v1/client/gallery/assets/{assetId}/poster` | `gallery_guest` read | video poster JPEG |
| `GET /api/v1/client/gallery/assets/{assetId}/download` | `gallery_guest` download | original attachment |
| `GET /api/v1/client/portal` | `portal_guest` | gallery/brand/usage-rights metadata |
| `GET /api/v1/client/workspace` | `workspace_guest` | non-draft child resource cards |
| `GET /api/v1/client/document` | exact `document_guest` variant | proposal, contract, or invoice summary |

Portal gallery cards deliberately contain no action URL or media link: portal
authority is non-transitive. Workspace cards contain only the same fixed child
links exposed by the existing browser workspace. Document reads never mark a
document viewed and never accept, sign, or charge; they return a same-origin HTTPS
fallback for the existing server-owned action page. Invoice totals, payments, and
balance are assembled from authoritative integer-cent rows without exposing Stripe
identifiers. Contract bodies are bounded and integrity-checked before the native
summary advertises an available action.

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
| `GET /api/v1/event-types/{id}/slots` | server-computed available slots |
| `GET /api/v1/bookings` | `Page<Booking>` |
| `GET /api/v1/ai/runs` | `Page<AIRun>` |
| `GET /api/v1/content/captions` | owner caption page |
| `GET /api/v1/content/captions/{id}` | owner caption detail |
| `GET /api/v1/galleries/{id}/cull` | implemented owner cull review page (`CullPage`) |

Owner endpoints in this table require the exact `studio_owner` principal with
`studio:read`. Native cull review is implemented in Milestone 5B.1 and the AI-run
ledger read is implemented in Milestone 5B.2. Native caption management is
implemented in Milestone 5B.3.

Collections default to 25 and cap at 100. Cursors carry ordering state but no
authorization; authorization is reevaluated on every page.

### Native owner AI activity (Milestone 5B.2)

`GET /api/v1/ai/runs?limit=25&cursor=...` is an owner-only, read-only projection
over the append-only AI provenance ledger. The request host selects the tenant
database; no tenant, subject, provider, or principal selector is accepted. Results
are ordered by run ID descending and use an opaque, signed cursor bound to the
authenticated tenant. The limit defaults to 25 and is constrained to 1–100.
Only `cursor` and `limit` are allowed, at most once each; unknown or duplicate
query parameters fail with `422 request.validation_failed` instead of being ignored.

The route deliberately has no detail endpoint, server filter, or command. The app
loads at most five 100-item pages and filters that latest-500 window locally. It
never polls, queues an offline AI command, or treats a successful run as approval.

    {
      "items": [
        {
          "id": 82,
          "capability": "vision",
          "provider": "argus",
          "status": "ok",
          "review": "human_review",
          "latency_ms": 1842,
          "cost_micro_usd": 2400,
          "tokens": 1180,
          "subject": {
            "kind": "gallery"
          },
          "created_at": "2026-07-11T14:30:00Z"
        }
      ],
      "next_cursor": null,
      "has_more": false
    }

`cost_micro_usd` is a non-negative integer count of one-millionth US dollars; a
missing provider report remains `null`, never zero. Invalid or negative metrics are
omitted. Unknown stored capabilities, statuses, and review classes map to `other`
or `unknown`. Provider is a closed normalized family (`argus`, `qwen`, `odysseus`,
`dionysus`, `aphrodite`, or `other`); arbitrary stored provider text and the raw
model column are never exposed. A subject contains only a closed generic kind
(`gallery`, `caption`, or `other`); the app supplies the localized label.
Tenant-local subject resource IDs, client-related titles, and mutable gallery data
are not exposed. Keeping the projection entirely append-only also lets the
first-page validator safely revalidate the bounded native feed. Unrecognized stored
subjects map to `other`.

The response never includes raw `error`, `model`, `correlation_id`,
`idempotency_key`, raw subject columns, prompts, generated output, captions,
validation notes, filenames, paths, provider run/job IDs, review URLs, endpoints,
payload fragments, or secrets.
Raw provider failures can contain upstream bodies, filesystem paths, or personal
data; truncation is not redaction. The normalized `status` is the entire failure
signal on this boundary.

Every `200` response uses `Cache-Control: private, no-cache`,
`Vary: Authorization`, and a strong representation `ETag`. `If-None-Match` can
return `304` with the same private headers. Authentication and tenant binding are
rechecked on every page. Malformed limits or cursors return the standard `422`
problem, and a cursor from another tenant is invalid even when both databases have
overlapping run IDs. The ETag includes a server projection version; changing any
normalization/redaction rule must bump it so a cached multi-page feed is rebuilt.
See the [AI activity operations runbook](IOS-AI-ACTIVITY-OPERATIONS.md).

### Native owner Content workspace (Milestone 5B.3)

Caption reads require exact 'studio:read'. Mutation and suggestion creation require
exact 'studio:write'. The request host selects the tenant; caption, plan, project,
and client joins are always resolved inside that tenant database.

| Method/path | Semantics |
| --- | --- |
| 'GET /api/v1/content/captions?limit=25&cursor=...' | newest-ID-first normalized page; limit 1–100 |
| 'GET /api/v1/content/captions/{id}' | normalized canonical detail |
| 'PATCH /api/v1/content/captions/{id}' | explicit draft-body save |
| 'POST /api/v1/content/captions/{id}/suggestions' | accept an asynchronous immutable operation |
| 'GET /api/v1/content/captions/{id}/suggestions/{uuid}' | poll the exact requesting session's operation |

A list page has this bounded shape:

~~~json
{
  "items": [
    {
      "id": 42,
      "version_id": "0123456789abcdef0123456789abcdef",
      "revision": 3,
      "client_display_name": "Avery Foods",
      "plan_title": "Monthly Social",
      "period": "2026-07",
      "label": "Hero",
      "body_preview": "Fresh summer plates...",
      "status": "draft",
      "ai_assisted": false,
      "updated_at": "2026-07-11T15:30:00Z"
    }
  ],
  "next_cursor": null,
  "has_more": false,
  "suggestions_enabled": false
}
~~~

The opaque 'version_id' prevents delete/reinsert ABA identity reuse; 'revision' is
monotonic for every web or mobile caption change. List/detail responses use
'Cache-Control: private, no-cache', 'Vary: Authorization', and a strong ETag.
'If-None-Match' may return an empty 304 with the same headers. The cursor is signed
and tenant-bound. Unknown or duplicate query parameters fail with 422.

The detail adds 'plan_id', full bounded 'body', optional 'note', 'ai_drafted_at',
'suggestions_enabled', and create/update timestamps. It never exposes the original
AI draft, raw provider/model/error, context/prompt, job/session/idempotency values,
delivery state, invoice/payment data, or a tenant selector.

A manual save body is:

~~~json
{
  "body": "Owner-reviewed caption text",
  "suggestion_id": null
}
~~~

The PATCH requires the detail's strong ETag in 'If-Match' and a canonical UUID
'Idempotency-Key'. It accepts only a non-empty body on a caption that is still
'draft'. A successful save increments revision, returns a new ETag, and does not
approve, publish, deliver, invoice, or charge. Exact retries replay the response;
payload/key reuse or a stale/weak/wildcard version returns 409 without mutation.

Suggestion creation additionally requires both the default-off
'MISE_MOBILE_CONTENT_SUGGESTIONS' switch and configured provider credentials. Its
optional body contains only a bounded owner instruction:

~~~json
{ "instruction": "Warm, concise, and suitable for Instagram." }
~~~

The server sends the provider only the bounded label, period, and optional
instruction. It creates the command, queued job, suggestion row, and content-free
audit evidence in one transaction and returns 202 plus a session-bound Location.
Daily and concurrent limits are tenant-local and use Retry-After on 429.

Operation responses have one exact shape:

~~~json
{
  "id": "a7b7edee-eae8-4e69-8eed-5e09adca80de",
  "caption_id": 42,
  "state": "ready",
  "review": "human_review",
  "candidate_text": "A short generated candidate.",
  "failure_reason": null,
  "base_revision": 3,
  "stale": false,
  "created_at": "2026-07-11T15:31:00Z",
  "expires_at": "2026-07-12T15:31:00Z",
  "completed_at": "2026-07-11T15:31:02Z"
}
~~~

'candidate_text' appears only for 'ready'. 'failure_reason' appears only for
'failed' and is one of 'disabled', 'provider_error', 'invalid_response',
'session_ended', 'unknown_outcome', or 'internal'. Raw provider/model/error/context
never crosses this boundary. Every operation response uses 'Cache-Control:
no-store' and 'Vary: Authorization'.

Generation never changes the caption. To save reviewed text, the app sends the
locally editable body plus the ready suggestion UUID through PATCH with the current
caption ETag and a separate stable idempotency key. The server requires the exact
requesting session, same caption identity/revision, ready/unexpired operation, and
draft status. It stores the chosen body, retains the original candidate as internal
AI provenance, marks the transient operation applied, scrubs provider/candidate
fields, and still leaves status as draft.

Queued-to-running is the sole paid-call claim. A recovered running operation becomes
'unknown_outcome' instead of calling the provider again. Session revocation,
expiration, caption deletion, kill-switch changes, and final-state races scrub or
discard transient output. See the
[Content suggestions operations runbook](IOS-CONTENT-SUGGESTIONS-OPERATIONS.md).

### Implemented owner commands (Milestone 4A)

| Method/path | Semantics |
| --- | --- |
| `GET /api/v1/clients/{id}` | editable client representation and `ETag` |
| `POST /api/v1/clients` | create a bounded mobile client record |
| `PATCH /api/v1/clients/{id}` | update bounded client fields with `If-Match` |
| `GET /api/v1/projects/{id}` | editable project representation and `ETag` |
| `POST /api/v1/projects` | create a project for a tenant-local client |
| `PATCH /api/v1/projects/{id}` | update title, stage, notes, and shoot date with `If-Match` |
| `GET /api/v1/tasks` | bounded owner task collection |
| `GET /api/v1/tasks/{id}` | editable task representation and `ETag` |
| `POST /api/v1/tasks` | create a task |
| `PATCH /api/v1/tasks/{id}` | update or complete a task with `If-Match` |
| `DELETE /api/v1/tasks/{id}` | delete a task with `If-Match` |

All commands require the exact owner `studio:write` capability and a UUID
`Idempotency-Key`. A key is bound to the authenticated API session, operation, and
request digest. Identical retries replay the original JSON response; reuse with a
different command or payload returns `409`. Updates/deletes also require the latest
strong `ETag` in `If-Match`; stale versions return `409` without mutation. Business
data, audit evidence, and the replay record share one transaction. Project workflow
effects are recoverable from the replay record after a post-commit dispatch failure.

### Implemented policy commands (Milestone 4B)

| Method/path | Authority and semantics |
| --- | --- |
| `GET /api/v1/bookings/{id}` | exact owner detail representation and `ETag` |
| `GET /api/v1/bookings/{id}/slots` | owner-only policy-filtered replacement slots |
| `POST /api/v1/bookings/{id}/cancel` | owner cancellation with `If-Match` |
| `POST /api/v1/bookings/{id}/reschedule` | atomically replace/cancel with `If-Match` |
| `POST /api/v1/client/proposal/accept` | exact proposal `respond` capability |
| `POST /api/v1/client/proposal/decline` | exact proposal `respond` capability |

Booking commands require `studio:write`. Proposal decisions are bound to the
authenticated document resource and never accept a proposal ID or tenant selector.
Every command requires a UUID `Idempotency-Key` and the strong representation
`ETag` in `If-Match`. Notification and workflow effects are written to the durable
job queue in the same transaction, leased by workers, retried after transient
failure, and recovered after restart. Client booking creation is intentionally not
part of this slice because no dedicated mobile booking/anti-abuse credential exists.

### Native owner culling (Milestone 5B.1)

The owner gallery detail includes `cull_enabled`. It is `true` only for an ordinary
gallery when the host-wide `MISE_CULL_UI` flag is enabled. It is always `false` in
the client-delivery manifest, and drops/transfers never become cull queues. The app
shows the native Review Cull action only when this server-derived value is true.

All cull routes resolve the tenant from the authenticated request host. They accept
no tenant ID, database path, slug, principal, or media path from the caller. Reads
require the exact `studio_owner` principal with `studio:read`; the decision command
also requires `studio:write`.

| Method/path | Contract |
| --- | --- |
| `GET /api/v1/galleries/{galleryId}/cull?limit=25&cursor=...` | score-ranked, cursor-paged `CullPage`; limit 1–100 |
| `GET /api/v1/galleries/{galleryId}/cull/assets/{assetId}/thumbnail` | private JPEG thumbnail; no original/download variant |
| `GET /api/v1/galleries/{galleryId}/cull/assets/{assetId}/preview` | private screen derivative JPEG; no original/download variant |
| `PATCH /api/v1/galleries/{galleryId}/assets/{assetId}/cull` | explicit keep/cut/restore decision |

A page contains every ready photo in the gallery, including cut photos so an owner
can restore them. Videos, pending/failed assets, drops, and other galleries are not
included. Scored photos sort by keeper score descending; unscored photos follow in
gallery position and asset-ID order. `counts` describes the full queue rather than
only the returned page:

    {
      "items": [
        {
          "asset_id": 201,
          "gallery_id": 17,
          "filename": "KLP_1024.jpg",
          "position": 1,
          "keeper_score": 0.98,
          "hero_potential": 0.86,
          "state": "keep",
          "thumbnail_url": "https://studio.example.com/api/v1/galleries/17/cull/assets/201/thumbnail",
          "preview_url": "https://studio.example.com/api/v1/galleries/17/cull/assets/201/preview",
          "media_revision": 73,
          "etag": "\"cull-asset-0123456789abcdef0123456789abcdef\""
        }
      ],
      "next_cursor": null,
      "has_more": false,
      "counts": {
        "total": 1,
        "keep": 1,
        "cut": 0,
        "undecided": 0,
        "scored": 1
      }
    }

The page response uses `Cache-Control: private, no-cache`, `Vary: Authorization`,
and a strong page `ETag`; `If-None-Match` may produce an empty `304`. The opaque
cursor is tenant- and gallery-bound and carries a signed snapshot of the
score-ranked queue.
If asset membership, position, or keeper scores change between pages, continuation
returns `409` with code `pagination.collection_changed`. The app must discard the
continuation and reload page one; it must not merge a changed ordering into the old
list. Authentication and authorization are reevaluated for every page.

Derivative responses use `Content-Type: image/jpeg`,
`Cache-Control: private, max-age=86400`, `Vary: Authorization`, and a strong media
`ETag`; `If-None-Match` may return `304`. The URL must remain on the exact tenant
origin and bind both gallery and asset IDs. The native media client rejects redirects,
credentials, queries, fragments, encoded paths, mismatched variants, client-gallery
paths in owner mode, and every owner-cull download/original path. A missing,
symlinked, unsafe, or cross-gallery derivative fails closed with `404`.

Each item has an opaque non-negative `media_revision`. It remains stable across cull
decisions, changes with the protected stored/derivative identity, and keys the
native in-memory image cache so replaced review media is not shown under stale item
state.

The decision body has one closed action:

    { "action": "cut" }

`keep` and `cut` store an explicit human decision. `restore` clears the cull state,
decision timestamp, and source; it does not delete or recreate a file. Every accepted
command requires both:

- `If-Match: "cull-asset-..."` using the strong item ETag; and
- `Idempotency-Key: <uuid>` stable for an exact retry.

The ETag covers the parent IDs, sanitized filename, scores, state, cull audit
revision, stored-file identity, and derivative identity. This prevents stale score
review, derivative replacement, ID reuse, and keep/cut/restore ABA transitions from
accepting an old card. A missing `If-Match` or UUID returns `422`; a stale/weak match
returns `409 resource.version_conflict`. An identical session-bound retry replays the
original representation with `Idempotency-Replayed: true`; key reuse with a different
action, resource, or ETag in that owner session returns
`409 request.idempotency_conflict`.

The decision, `mobile_owner` audit row, content-revision bump when state changes,
and replay record share one immediate transaction. Success returns the new item and
ETag with `Cache-Control: no-store` and `Vary: Authorization`. A cut is a reversible
delivery flag: while `MISE_CULL_UI=true`, client gallery/media/download/favorite/ZIP
paths exclude it, but the original and derivatives remain intact and the owner queue
still shows it. See the [native cull operations runbook](IOS-AI-CULL-OPERATIONS.md)
for rollout and rollback behavior.
If the protected media changes during a decision, the command returns
`409 cull.media_changed` and the transaction leaves no decision, audit, or replay
residue.

When `MISE_CULL_UI=false` (the default), `cull_enabled` is false, every cull list,
media, and decision route returns `404`, and client delivery ignores stored cull
states. Common failures otherwise follow the standard problem shape: `401` expired
session, `403` wrong principal/scope, `404` disabled/missing/cross-parent resource,
`409` collection/version/idempotency/media conflict, `422` malformed input, and
`429` with `Retry-After` for rate limiting.

## Remaining command roadmap

| Method/path | Semantics |
| --- | --- |
| `POST /api/v1/contracts/{id}/sign` | hash/version checked signature evidence |
| `POST /api/v1/invoices/{id}/checkout` | return server-created hosted checkout URL |
| `POST /api/v1/bookings` | atomically revalidate slot and create booking |
| `POST /api/v1/captions/{id}/draft` | explicit AI draft; never auto-approve |

Contract signing, checkout, booking, rescheduling, and AI commands require an
`Idempotency-Key`. The server retains transition rules, amount math, hash checks,
Stripe reconciliation, audit logging, and workflow dispatch.

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
            "thumbnail_url": "https://studio.example.com/api/v1/client/gallery/assets/201/thumbnail",
            "preview_url": "https://studio.example.com/api/v1/client/gallery/assets/201/preview",
            "poster_url": null,
            "download_url": "https://studio.example.com/api/v1/client/gallery/assets/201/download"
          },
          "alt_text": "A couple during their ceremony",
          "keywords": ["ceremony", "couple"],
          "keeper_score": 0.98,
          "hero_potential": 0.86,
          "cull_state": "keep"
        }
      ],
      "hero_asset_ids": [201],
      "vision": null,
      "cull_enabled": true
    }

Never expose gallery/portal PINs, `stored` filenames, or server paths. Media URLs
must enforce bearer scope, gallery publication/expiry, asset parentage/readiness,
and the cull delivery gate. Support Range requests for video and conditional/private
caching. Do not put bearer credentials in signed URL query parameters.

The owner `GET /galleries/{id}` manifest deliberately continues to emit `null`
for every ordinary gallery media link; owner cull derivatives are exposed only by
the exact cull routes above. Its `cull_enabled` value is server-derived. The
capability-bound `GET /client/gallery` manifest always returns
`cull_enabled=false` and emits only variants that physically exist beneath the
tenant media root. Each media request rechecks the exact bearer resource, visitor
binding, gallery publication and expiry, ready status, asset parent, section parent,
cull gate, and requested
download scope. Missing or unsafe files fail closed.

## Offline/cache metadata

Successful detail/collection responses should include:

- `ETag` derived from a representation revision
- `Last-Modified` where meaningful
- `Cache-Control: private, no-cache` for manifests (store allowed, revalidate)
- `Cache-Control: private, max-age=86400` plus ETag for derivative media variants
- `Cache-Control: private, no-cache` for original downloads

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
