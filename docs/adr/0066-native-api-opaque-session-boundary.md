# ADR 0066 — Native API opaque session boundary

**Status:** Accepted
**Date:** 2026-07-10
**Deciders:** Kevin (owner), iOS/full-stack architect

## Context

Mise's browser application authenticates owners and clients with host-bound cookies
and renders HTMX/HTML. A native client needs JSON responses, background-safe session
renewal, device revocation, and narrowly scoped client capabilities. Reusing browser
cookies would couple the app to HTML redirects and CSRF behavior. A self-contained JWT
would make password/PIN rotation, unpublishing, and replay detection harder because
authority would remain in the signed payload until it expired.

Hosted Mise also uses one physical SQLite database per studio. The request host must
select that database before a mobile credential is examined; no caller-supplied tenant
identifier may become routing authority.

## Decision

- Mount a dedicated FastAPI application at `/api/v1`. It publishes a scoped OpenAPI
  document and only JSON / RFC 9457-style problem responses. The existing parent
  middleware continues to resolve the tenant, enforce billing state, rate-limit, stamp
  security headers, and issue a request correlation id.
- Use `Authorization: Bearer` exclusively. Browser cookies are never an authentication
  fallback for native endpoints.
- Issue 256-bit opaque access and refresh credentials. Persist SHA-256 token hashes,
  never the credentials. Access tokens live for 15 minutes; refresh tokens rotate with
  a 30-day rolling expiry inside a 90-day absolute family cap. Reusing a consumed
  refresh token atomically revokes the entire family.
- Bind each family to both the immutable hosted tenant id and normalized request origin
  (or to the origin for self-hosted installs). Authentication happens only after the
  parent middleware has selected the tenant database.
- Revalidate live authority on every access/refresh: owner password fingerprint,
  resource PIN/capability, published state, gallery expiry, and session revocation.
  Credential changes revoke the affected family.
- Model only the authority Mise has today: `studio_owner`, `gallery_guest`,
  `portal_guest`, `workspace_guest`, and `document_guest`. Every guest carries exact
  resource scopes. A portal is not a client account; a workspace does not implicitly
  unlock its gallery.
- Store a hash of the app installation id plus bounded display-only device metadata.
  Session-list responses never expose the raw id or hash. The iOS client keeps the
  complete session in a ThisDeviceOnly Keychain item scoped to the server origin and
  uses Face ID / Touch ID only as a local re-entry lock.

## Consequences

- Password/PIN rotation, unpublish, explicit logout, device revocation, and refresh
  replay all take effect server-side without waiting for a long-lived signed token.
- Tenant isolation has two independent checks: physical database selection and the
  stored tenant/origin binding.
- Native authentication can evolve without changing the browser cookie contract.
- Session/token rows accumulate while a family is retained. A later maintenance slice
  may purge families past the absolute expiry after an audit-retention window; it must
  retain consumed refresh hashes long enough for replay detection.
- Staff memberships and durable client accounts remain separate schema/product
  decisions. Add them only when the backend has explicit membership and permission
  models, not by widening these principals.

## Alternatives considered

- **Reuse browser cookies / wrap the website.** Rejected: redirects, shared cookie
  state, CSRF assumptions, and HTML contracts are a fragile native security boundary.
- **Self-contained JWT access and refresh tokens.** Rejected for the first-party app:
  opaque server-side state gives immediate revocation and straightforward rotation
  reuse detection with the current SQLite architecture.
- **One broad `client` principal.** Rejected: Mise has resource links and PINs, not a
  verified cross-resource client identity. Inferring one would silently widen access.
- **Caller-selected tenant headers.** Rejected: host-first database selection is the
  existing physical-isolation model and remains authoritative.
