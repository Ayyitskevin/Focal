# ADR 0067 — Native push notification and deep-link boundary

**Status:** Accepted
**Date:** 2026-07-11
**Deciders:** Kevin (owner), iOS/full-stack architect

## Context

Mise now has host-bound opaque mobile sessions, native owner workflows, and exact
client capabilities. Transactional events such as bookings, proposal responses,
and payments need timely owner notifications without turning a device token,
installation identifier, or deep link into authentication or tenant-selection
authority.

APNs delivery is asynchronous and at-least-once. Provider requests require HTTP/2,
TLS, token-based ES256 authentication, durable retry state, and invalid-token
handling. The app may receive a notification tap before authentication restoration
or biometric unlock completes. Universal links are also untrusted external input
and arbitrary custom tenant domains cannot be added dynamically to a signed iOS
associated-domains entitlement.

## Decision

- Milestone 5A registers only `studio_owner` sessions. Existing guest principals
  remain exact-resource capabilities rather than a durable cross-resource client
  identity; client push requires a separate recipient model.
- Store push registrations in each tenant product database. A registration is
  bound to the authenticated session and the session's existing installation hash.
  Tenant, principal, session, topic, and origin are server-derived and never
  caller-selected.
- The app submits the raw installation UUID only during registration. Read,
  preference, and delete operations use `/api/v1/devices/current`, preventing the
  identifier from entering route/access logs. The server never returns the
  installation identifier, APNs token, token hash, or ciphertext.
- Persist a keyed token fingerprint for uniqueness and authenticated-encrypted
  token material for delivery. Raw APNs tokens remain memory-only on iOS and are
  forwarded again whenever APNs issues a token.
- Snapshot eligible devices into per-device delivery rows in the same transaction
  as the business event. Stable event keys and unique event/device pairs prevent
  webhook, command-replay, and worker concurrency duplicates. Devices registered
  after an event do not receive it retroactively.
- Delivery uses a leased durable worker with persisted exponential backoff. Every
  send rechecks the session's revocation, absolute expiry, live credential
  fingerprint, registration state, token version, and current preference. APNs
  invalid-token responses deactivate only the compared token version.
- APNs provider credentials and topic are server configuration. The provider stays
  dormant unless the complete configuration is present. Payloads contain generic
  lock-screen text plus a bounded, versioned navigation intent; they never contain
  names, email, phone, notes, amounts, locations, capability slugs, credentials, or
  arbitrary URLs.
- A deep link is navigation intent, never authority. The iOS router validates the
  exact HTTPS origin, workspace namespace, principal kind/id, scopes, route shape,
  and positive resource identifiers before fetching through the origin-bound API.
  Taps wait behind session restoration and biometric unlock. Cross-workspace links
  require an explicit authentication/switch decision and never reuse the current
  bearer token.
- Notification permission is requested only from an explanatory user action.
  Previously authorized installations register with APNs only while an eligible
  owner session is bound; signed-out launches/foreground transitions do not register.
  Logout immediately clears routes/notifications, cancels synchronization, and calls
  iOS APNs unregistration without waiting for connectivity. The authenticated logout
  transaction remains the server-side session/device revocation boundary when online.
- Events are deliverable for seven days. The tenant sweep retains expired diagnostics,
  terminal APNs jobs, and inactive device metadata for a bounded configurable period
  (30 days by default), then purges them. Billing-locked retained tenants run cleanup
  without scheduling delivery work.
- The first associated-domains entitlement covers only the controlled hosted apex
  and wildcard tenant domains. Each serves a no-redirect AASA document. Custom and
  self-hosted origins retain HTTPS/browser or explicit link-exchange fallback.

## Consequences

- New booking, booking-change, proposal-response, and payment notifications are
  recoverable across restarts and isolated per tenant/device.
- A crash after APNs accepts a request but before the delivery row commits can still
  duplicate a visible notification. Stable APNs IDs and collapse IDs mitigate this;
  neither APNs nor Mise claims exactly-once delivery.
- Device registration adds no authority and cannot outlive the session or live
  credential that created it.
- Offline logout cannot revoke unreachable server state, but local APNs unregistration
  and owner-binding-gated re-registration prevent that signed-out install from
  displaying later old-account alerts. Generic copy remains the containment for an
  alert that was already in flight when logout began.
- Production delivery still requires Apple team/key/topic configuration, matching
  provisioning, hosted AASA deployment, and sandbox plus TestFlight device tests.
- Client-facing push, silent background refresh, and arbitrary custom-domain
  universal links remain explicit future product/security decisions.

## Alternatives considered

- **Treat an APNs token as a user identity.** Rejected: tokens rotate, can be shared
  across app sessions, and confer no Mise authority.
- **Register all guest capabilities as one client.** Rejected: Mise has resource
  capabilities, not a verified client account; joining them would widen access.
- **Send directly from request handlers after commit.** Rejected: crashes lose the
  notification and webhook retries can duplicate it.
- **Put tenant IDs or arbitrary URLs in the payload.** Rejected: request host and
  active session remain authoritative; external routes are untrusted input.
- **Use the existing immediate three-attempt job retry as APNs backoff.** Rejected:
  APNs requires persisted delay/retry state and per-device failure isolation.
