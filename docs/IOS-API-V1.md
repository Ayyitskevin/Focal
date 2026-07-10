# Mise mobile API v1 contract

This document is the contract targeted by the native app. Milestone 1 implements
tenant discovery, every authentication/capability route below, session management,
and the scoped OpenAPI document. Milestone 2 adds the owner dashboard, client and
project collections, gallery manifests, event types, and booking agenda. The
remaining reads and commands stay in the planned Milestones 3–4 contract.

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
      }
    }

The same generic 401 problem covers unknown email and wrong password. Reuse the
current IP lockout and tenant password verifier.

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
| `GET /api/v1/event-types/{id}/slots` | server-computed available slots |
| `GET /api/v1/bookings` | `Page<Booking>` |
| `GET /api/v1/ai/runs` | `Page<AIRun>` |
| `GET /api/v1/galleries/{id}/cull` | paged cull deck/results |

The Milestone 2 endpoints are available only to the exact `studio_owner`
principal with `studio:read`. Client/project detail, document, slot, AI-run, and
cull-result reads remain reserved contract surface until their delivery slices.

Collections default to 25 and cap at 100. Cursors carry ordering state but no
authorization; authorization is reevaluated on every page.

## Initial commands

| Method/path | Semantics |
| --- | --- |
| `PUT /api/v1/galleries/{g}/assets/{a}/favorite` | idempotently select |
| `DELETE /api/v1/galleries/{g}/assets/{a}/favorite` | idempotently unselect |
| `POST /api/v1/galleries/{g}/assets/{a}/comments` | add video comment/reply |
| `POST /api/v1/proposals/{id}/accept` | server-authoritative transition |
| `POST /api/v1/proposals/{id}/decline` | server-authoritative transition |
| `POST /api/v1/contracts/{id}/sign` | hash/version checked signature evidence |
| `POST /api/v1/invoices/{id}/checkout` | return server-created hosted checkout URL |
| `POST /api/v1/bookings` | atomically revalidate slot and create booking |
| `POST /api/v1/bookings/{id}/cancel` | cancel within policy |
| `POST /api/v1/bookings/{id}/reschedule` | atomically create replacement/cancel old |
| `PATCH /api/v1/galleries/{g}/assets/{a}/cull` | owner keep/cut/restore command |
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

Milestone 2 deliberately emits `null` for every media link. Authenticated thumbnail,
preview, video, and download routes arrive with the client-delivery slice rather
than exposing the existing browser/file boundary to the native app.

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
