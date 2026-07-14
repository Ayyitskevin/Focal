# Mise for iOS — architecture and delivery plan

Status: Milestones 1–3 implemented; Milestone 4a backend mutations in progress
Design source: the "Mise Mobile" design handoff (owner + client, iPhone/iPad,
light/dark) is the visual reference for screens, copy tone, and tokens
Minimum OS: iOS 17 / iPadOS 17
UI: SwiftUI
State: Observation-based MVVM with async/await
Backend: the existing FastAPI service remains the source of truth

## 1. Repository findings that shape the app

Mise historically had no user-facing mobile API. Milestone 1 now adds a deliberately
small native authentication boundary:

- a mounted `/api/v1` FastAPI application with scoped OpenAPI;
- tenant discovery, owner and capability exchanges, refresh/logout/me, and owner
  device-session revocation;
- opaque rotating bearer sessions bound to the tenant and request origin.

The findings that still shape every later feature route are:

- `app/main.py` disables OpenAPI and registers the admin, public, and machine APIs
  into one application.
- `app/service_api.py` is a small service-to-service surface protected by the
  process-wide Argus and shots tokens. Those secrets must never ship in an app.
- Admin, gallery, portal, workspace, proposal, contract, invoice, and scheduling
  flows are primarily HTML, form posts, redirects, HTMX fragments, and cookies.
- Hosted tenancy is selected from the request host. `app/saas.py` then places the
  tenant's SQLite path and media directories in request-local context variables.
  Entity IDs are only unique inside that tenant database.
- A studio has one owner password today. There is no staff membership/role model.
- There is no general client account. Gallery visitors, portals, workspaces, and
  document links are independent capabilities.

The native app targets this `/api/v1` contract. It must not scrape
HTML, reuse the global machine tokens, or broaden a resource PIN into a fictional
client-wide identity.

## 2. Architecture decision

Use a feature-oriented MVVM architecture with a small dependency container:

    SwiftUI view
        -> @Observable feature model
        -> repository protocol
        -> actor-isolated API client / local cache
        -> FastAPI /api/v1

Reasons:

- SwiftUI and Observation are enough for view state on iOS 17; Combine remains
  useful only at system boundaries that still publish streams.
- Actor-isolated networking and session refresh make concurrent requests and token
  rotation easier to reason about.
- Repositories keep transport DTOs away from views and provide a clean seam for
  SwiftData/file-backed offline behavior.
- This app does not yet need TCA's additional dependency and reducer surface.
  Features can migrate later if state coordination becomes materially more complex.

### App layers

| Layer | Responsibility |
| --- | --- |
| App | composition root, session phase, scene handling, navigation, deep links |
| Features | SwiftUI screens and one `@Observable` model per user-facing flow |
| Domain | tenant-scoped entities, money, date-only values, permissions |
| Repositories | role-aware use cases, pagination, optimistic UI, cache policy |
| Networking | typed endpoints, JSON conventions, bearer retry, problem decoding |
| Security | Keychain persistence, rotating refresh, local biometric app lock |
| Persistence | SwiftData metadata/forms; protected files for media and downloads |
| System | APNs, background tasks, calendar export, share sheets, reachability hints |

## 3. Identity and tenant isolation

### Studio owner

The first native studio principal is `studio_owner`. Do not add manager/staff roles
until the backend has memberships and permissions.

1. The app obtains a canonical tenant URL from a typed URL, QR code, invite email,
   or universal link.
2. `GET /api/v1/tenant` returns non-secret branding and supported auth methods.
3. `POST /api/v1/auth/studio/login` validates the existing owner password on that
   tenant host. Hosted mode may also require the stored owner email, but failures
   must remain non-enumerating.
4. The server returns a short-lived opaque access token and a rotating opaque
   refresh token.
5. Every protected request resolves the tenant from the host first, then verifies
   the token's immutable tenant ID against that context.

The tenant ID, tenant slug, or a custom `X-Tenant` header supplied by the caller
must never select a database.

### Shared client access

The initial client principals intentionally mirror current authority:

- `gallery_guest`: one gallery and one server-side visitor
- `portal_guest`: one portal; it does not automatically unlock linked galleries
- `workspace_guest`: one published workspace and its non-draft document links
- `document_guest`: one proposal, contract, or invoice capability

Use separate unlock/exchange endpoints and reuse the existing constant-time PIN
comparison plus durable lockout history. A future authenticated `client_account`
requires an explicit product/schema project; it is not inferred from `client_id`.

### Token lifecycle

- Access token: opaque, 10–15 minute lifetime, held in memory and in the encrypted
  session record only to support process restoration.
- Refresh token: at least 256 random bits, about 30 days, absolute lifetime cap.
- Server stores token hashes only.
- Refresh rotates atomically. Reuse of a consumed token revokes the token family.
- Password reset, PIN rotation, unpublish, deletion, or explicit logout revokes the
  relevant session.
- Face ID / Touch ID is a local re-entry gate, not server authentication.

The checked-in iOS foundation stores the session in a ThisDeviceOnly Keychain item.
The refresh token is available after first device unlock so background refresh can
work; biometric policy is applied as an app lock instead of making APNs/background
work depend on an interactive Keychain prompt.

## 4. API boundary

`docs/IOS-API-V1.md` defines the first contract. Core rules:

- Pydantic request/response DTOs; never serialize SQLite rows directly.
- JSON booleans, integer cents/minor units, RFC 3339 UTC instants, and explicit
  `YYYY-MM-DD` local dates.
- Cursor pagination for collections.
- RFC 9457-style problem details with stable machine codes and field errors.
- JSON 401/403 responses; never redirect an API request to `/admin/login`.
- Named commands for lifecycle transitions (accept, sign, checkout, reschedule),
  rather than generic status patches.
- Server-advertised `available_commands` gate optional native controls; a client
  never infers command availability from app version or a cached screen.
- `Idempotency-Key` for every retryable mutation with business consequences.
- Consequential external effects use tenant-local durable state, bounded status
  reads, and a manual override. Provider acceptance is at-least-once unless the
  provider supplies a transactional idempotency guarantee.
- `ETag`/`If-None-Match` and `content_rev` for cache validation.
- Media paths are generated by the server. Never return `stored`,
  `originals_path`, PINs, Stripe IDs, or signer IPs.

The existing web routes remain intact. Shared query/business functions should be
extracted behind both the HTML routes and new DTO routers to prevent drift.

## 5. Native information architecture

The root UI changes with the principal, not just with screen visibility.

### Studio owner

- Home: KPIs, action items, activity, upcoming shoots/bookings, quick create.
- Studio: projects/pipeline and CRM clients.
- Calendar: month/agenda on iPhone; split calendar/agenda on iPad.
- Galleries: delivery state, upload/progress, cull/vision, proofing activity.
- More: proposals, contracts, invoices, financials, AI/content, settings.

### Shared client access

- Home: the exact unlocked portal/workspace/document capability.
- Galleries: native grid, section progress, favorite, comment, lightbox, download.
- Documents: proposals, contracts, invoices, receipts.
- Bookings: available slots and existing booking management when in scope.
- Settings: notification preferences, cache/download management, privacy, sign out.

Use `NavigationSplitView` for iPad and compact navigation/tab presentation on
iPhone. Keep route values typed so APNs and universal links resolve through the
same authorization-aware router.

## 6. Offline and caching policy

Offline is cache-first for safe reads, deliberately narrow for writes.

| Data | Offline behavior |
| --- | --- |
| Dashboard/CRM/document summaries | show last successful snapshot with age |
| Gallery manifests/thumbnails | URLCache plus protected disk cache |
| Explicit gallery downloads | background URLSession into protected app files |
| Draft intake/forms/notes | SwiftData draft; queued submit with idempotency key |
| Favorites/comments | optimistic only when queued operation has a stable ID |
| Contract signatures/payments | never complete offline; server-confirmed only |
| Booking | draft selection offline, but revalidate and book online atomically |
| Booking reschedule | online only; preserve one UUID key across transport retries, then follow the returned workflow status |
| AI commands | online command; cache result/status for later display |

Every persisted/cache key includes `workspace.cacheNamespace` plus the tenant-local
entity ID. Logout, session revocation, or workspace change purges private cached
files and queued writes.

Use server validators instead of polling full payloads:

- gallery `contentRevision` and ETag
- collection cursor plus `updated_since` where deletion tombstones are available
- background refresh only for user-visible, time-sensitive data

## 7. Push notifications and deep links

Backend additions:

- `POST /api/v1/devices`: upsert APNs token, installation ID hash, environment,
  locale, app version, principal/session, and preferences.
- `DELETE /api/v1/devices/{installationId}`: unregister on logout.
- tenant-local notification outbox and delivery log.
- APNs provider using token-based authentication; background jobs must enter the
  correct `tenant_runtime` before reading data.

The app asks for notification permission after the user encounters a meaningful
benefit, not on first launch. It requests a fresh APNs token each launch and sends
it to the backend; the app does not treat a cached token as authoritative.

Supported links should use HTTPS universal links:

- `/app/home`
- `/app/projects/{id}`
- `/app/galleries/{id}/assets/{id}`
- `/app/invoices/{id}`
- `/app/bookings/{id}`
- existing `/g`, `/portal`, `/w`, `/p`, `/c`, and `/i` links as exchange/fallback
  entry points

The router first verifies the active workspace and principal scope. If the link
belongs to another tenant, it starts a deliberate workspace/auth exchange instead
of reusing the current bearer token.

## 8. Security and privacy baseline

- TLS only in production; ATS exceptions are debug-only and limited to localhost.
- Keychain stores tokens. UserDefaults and SwiftData never contain credentials,
  PINs, payment secrets, or document capability slugs.
- Private files use complete-until-first-authentication data protection; the most
  sensitive exports use complete protection.
- Access tokens never appear in query strings, analytics, crash breadcrumbs, or
  logs. Logger privacy defaults to private for identifiers.
- No certificate pinning initially; it creates an outage-prone rotation path.
  Rely on ATS and a correctly managed public TLS chain.
- Reject redirects for `/api/v1` so a 303 login page cannot masquerade as a 200 API
  response and authorization cannot be redirected unexpectedly.
- Keep `/api/v1` on explicit auth and general API rate buckets as the route surface grows.
- Resource queries bind child and parent IDs and reapply published/ready/cull gates.
- High-value commands include actor/session/device in the audit event.
- Stripe checkout remains hosted for the first release. The app displays server
  totals and opens the server-issued checkout URL; it never recomputes the charge.
- Native e-sign is a separate legal/security milestone. Preserve document hash,
  typed consent, timestamps, audit evidence, and preferably email OTP/magic-link
  verification before replacing the web flow.

## 9. Accessibility, appearance, and performance

- Dynamic Type through accessibility sizes; never encode key content in fixed cards.
- VoiceOver labels/values for status, amounts, gallery position, and selection state.
- Minimum 44-point controls, keyboard navigation on iPad, visible focus, and
  alternatives to drag/swipe-only actions.
- Do not convey invoice/cull status with color alone.
- Respect Reduce Motion, Reduce Transparency, Increased Contrast, and Differentiate
  Without Color.
- Semantic system colors and materials provide light/dark mode by default; tenant
  accent colors are contrast-checked before use.
- Gallery grids use paged manifests, thumbnail variants sized for display scale,
  prefetching, cancellation on disappear, and bounded decoded-image memory.
- Video uses AVPlayer with Range-capable authorized media endpoints.

## 10. Delivery sequence

### Milestone 0 — checked-in foundation (complete)

- XcodeGen project definition for iOS 17 and strict Swift concurrency
- tenant/principal-aware auth and domain DTOs
- async URLSession API client, typed problems, one-time 401 refresh retry
- Keychain session persistence and actor-serialized refresh
- endpoint catalog and mocked transport tests

### Milestone 1 — backend contract and authentication (complete)

- red-light security/schema PR: `/api/v1` router, API session/token tables,
  rotation/revocation, rate limits, tenant/billing allowlists, Pydantic/OpenAPI
- tenant setup plus studio login/logout/refresh/me
- gallery/portal/workspace/document capability exchanges
- app session state, workspace setup, biometric app lock

### Milestone 2 — owner read-only companion (implemented in this PR)

- dashboard, projects, clients, gallery manifests, calendar agenda
- cache-first repositories and stale-state UI
- iPhone/iPad navigation, dark mode, accessibility baseline

### Milestone 3 — client delivery (implemented; ADR 0067)

- `ClientCompanionView`: Home / Gallery / Documents / Bookings tabs for all
  four guest principals, each tab scoped to exactly the unlocked capability
- shared owner/client sectioned gallery grid + fullscreen paging lightbox;
  optimistic gallery-guest favoriting with server-confirmed proofing counts
- bearer-authenticated media loading (`AuthenticatedMediaLoader` /
  `AuthenticatedRemoteImage`) — owner manifests render real thumbnails too
- client Home renders server-computed next steps; Documents shows
  proposal/contract/invoice detail with web fallback for accept/sign/pay
- design tokens from the handoff (`MiseDesign`): terra accent with the
  intentional dark-mode hue shift, semantic status-pill families, serif
  display type via the system serif design
- deferred within M3 scope: video comments, background downloads, and
  document-level deep links from next steps (currently tab-level); the
  handoff's decorative lightbox comment/download buttons are intentionally
  not shipped

### Milestone 4 — safe mutations

- owner task completion and booking cancellation implemented and wired in the
  draft native slice
- atomic booking reschedule plus the S6e durable client-calendar workflow are
  implemented in stacked red-light backend drafts: old CANCEL gates replacement
  REQUEST, completed effects survive retries, and status/manual retry are
  owner-only. Persisted tenant-scoped calendar identity, canonical lifecycle
  supersession across mobile/public/admin, post-lock retry authorization/audit,
  expired-lease lifecycle recovery while delivery is disarmed, and existing-only
  retained-tenant recovery close the crash/race boundaries;
  native reschedule wiring and deliberate server activation remain queued after
  human review
- CRM/project edits and proposal decisions remain planned
- idempotency, optimistic queues where safe, audit coverage
- native e-sign only after legal/security review

### Milestone 5 — operations

- APNs device registry/outbox, preferences, deep links
- AI run/cull/content previews and explicit commands
- telemetry, performance budgets, TestFlight rollout, App Store privacy artifacts

## 11. Product decisions still open

These do not block the implemented foundation and owner companion:

1. Production root domain, bundle ID, Apple team ID, and final app/display name.
2. Whether owner login remains password-only or adds owner email as an identifier.
3. Whether the first App Store release supports self-hosted/custom-server installs.
4. Client product direction: keep shared access only, or fund true client accounts.
5. Which legal jurisdictions and signature assurance level native e-sign must meet.
