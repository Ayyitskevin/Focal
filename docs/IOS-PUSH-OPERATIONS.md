# Mise native push operations

This runbook is the release and incident checklist for Milestone 5A. Push is an
owner-only convenience channel: the FastAPI API and active owner session remain the
authority, and disabling APNs must never disable the rest of Mise.

## 1. Apple and signing prerequisites

1. Use the same explicit App ID/bundle ID in Apple Developer, `ios/project.yml`,
   `MISE_APNS_TOPIC`, and the release provisioning profile.
2. Enable Push Notifications and Associated Domains for that App ID.
3. Create an APNs signing key, recording its 10-character key ID and team ID. Store
   the `.p8` only in the production secret manager; it is not an iOS asset.
4. Confirm the Debug profile carries `aps-environment=development` and Release/TestFlight
   carries `aps-environment=production`.
5. Replace `mise.example` in both xcconfig files. The checked-in associated-domains
   entitlement intentionally covers only the controlled hosted apex and wildcard.
   Custom/self-hosted origins continue through browser/link-exchange fallback.

The app never contains an APNs provider key. It contains only the signed entitlement
that lets iOS obtain a device token for this bundle.

## 2. Backend configuration and deploy order

Deploy the backend/migration before distributing the app. Install the pinned
requirements, migrate every tenant product database, then restart the web/worker
processes with these secrets:

    MISE_APNS_TEAM_ID=A1B2C3D4E5
    MISE_APNS_KEY_ID=F6G7H8J9K0
    MISE_APNS_TOPIC=com.ayyitskevin.mise
    MISE_APNS_ENVIRONMENT=production
    MISE_APNS_PRIVATE_KEY_PATH=/run/secrets/AuthKey_F6G7H8J9K0.p8
    MISE_APNS_TOKEN_ENCRYPTION_KEY=<standard-base64-32-random-bytes>
    MISE_APNS_TIMEOUT_SECONDS=10
    MISE_APNS_MAX_ATTEMPTS=8
    MISE_APNS_RETRY_BASE_SECONDS=60
    MISE_APNS_RETRY_MAX_SECONDS=21600
    MISE_APNS_LEASE_SECONDS=300
    MISE_APNS_SWEEP_SECONDS=60
    MISE_APNS_RETENTION_DAYS=30

Generate the independent token-storage key once:

    python3 -c 'import base64,secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())'

Alternatively, set `MISE_APNS_PRIVATE_KEY_B64` to standard-base64 `.p8` bytes.
Configure exactly one provider-key source. Never reuse the APNs `.p8` as the token
storage key, and never print either value during health checks or deploys.

One backend process accepts exactly one APNs environment. A Debug build sends
`sandbox` and cannot register against a production-mode deployment. Use a separate
staging tenant/backend configured with `MISE_APNS_ENVIRONMENT=sandbox` for development
device tests (or deliberately switch and restart a non-production deployment). Never
flip the production deployment to sandbox while production registrations are active.

`084_mobile_push_notifications.sql` creates tenant-local devices, events, and
deliveries. The scheduler polls due push delivery every minute while retaining the
ordinary recurring-work cadence. Jobs carry only a tenant-local delivery ID; hosted
workers re-enter that tenant runtime before reading encrypted token material.
Each event is deliverable for seven days. After expiry, a worker records
`skipped/event_expired`; expired event/delivery diagnostics, terminal APNs jobs, and
inactive device metadata are purged after the configured retention window (30 days
by default). Active registrations remain only while their owner session is current.

## 3. AASA verification

Both controlled endpoints must return `200`, `application/json`, and no redirect on
the apex and a real tenant host:

    curl -i --max-redirs 0 https://mise.example/.well-known/apple-app-site-association
    curl -i --max-redirs 0 https://north-star.mise.example/apple-app-site-association

Verify the document's app identifier is `<TEAM_ID>.<BUNDLE_ID>`, `/app/*` is present,
and the exact shared capability paths remain present. A missing/invalid server team
ID or topic intentionally returns `404` rather than publishing a wrong association.
Recheck from Apple's CDN/device network after DNS, TLS, or AASA changes; cached AASA
state is not proof that the current origin is correct.

## 4. Automated release gates

Backend gates from the repository root:

    .venv/bin/ruff check app tests
    .venv/bin/pytest -q tests/test_apns.py tests/test_push_notifications.py \
      tests/test_mobile_devices_api.py tests/test_notification_event_hooks.py \
      tests/test_associated_domains.py tests/test_mobile_api.py \
      tests/test_mobile_auth.py tests/test_mobile_policy_mutation_api.py

iOS gates on macOS with current Xcode/XcodeGen:

    cd ios
    xcodegen generate
    xcodebuild test -project Mise.xcodeproj -scheme Mise \
      -destination 'platform=iOS Simulator,name=iPhone 16'

Also archive Release once to verify entitlements, signing, `PrivacyInfo.xcprivacy`,
and the production APNs environment in the built product—not only in source YAML.

## 5. Physical-device acceptance

### Development/sandbox build

Point Debug at the sandbox-configured staging backend described in section 2. A
production-configured backend will reject the Debug registration by design.

1. Install a Debug build on a real device. Sign in as an owner; a guest capability
   must never register a push device.
2. Confirm there is no first-launch permission prompt. Open Account → Notification
   settings, read the explanation, then enable notifications.
3. Verify the server returns a token-free device representation and the device row
   stores ciphertext rather than the raw token. Never copy the token into a ticket.
4. Trigger one new booking, client cancellation/reschedule, proposal response, and
   reconciled payment. Confirm lock-screen copy is generic—no names, contact data,
   location, notes, amounts, slugs, or credentials.
5. Tap project and booking alerts from foreground, background, terminated, and
   biometric-locked states. Each must wait for auth/unlock and fetch through the
   typed API route. A different-workspace universal link must require confirmation.
6. Disable one preference and prove that category no longer snapshots a delivery
   for the device. Exercise a stale `ETag` update and confirm the UI reloads.
7. Deny notifications in Settings, foreground the app, and confirm server
   deregistration. Re-enable, foreground, and confirm a fresh APNs callback binds
   the device again.
8. Sign out while online and offline. Local routes/delivered notifications must clear,
   iOS must unregister from APNs immediately, and a signed-out foreground/relaunch
   must not register again. Online server logout must revoke the session and erase
   ciphertext. Offline logout cannot prove remote revocation, so confirm the local
   APNs boundary suppresses future old-account alerts and review the server session
   audit/absolute-expiry path separately.

### TestFlight/production

Repeat the full matrix with a TestFlight build and `MISE_APNS_ENVIRONMENT=production`.
Sandbox success does not validate the production topic, host, key, or entitlement.
Include token rotation/reinstall, two-device preference isolation, tenant billing
lock/recovery, invalid-token deactivation, and a worker restart with queued delivery.

## 6. Privacy-safe operations

Use aggregate queries inside each tenant database; never select token, hash,
ciphertext, installation hash, session ID, payload JSON, or client business rows
into logs or dashboards:

    SELECT active, COUNT(*) FROM mobile_push_devices GROUP BY active;
    SELECT status, reason, COUNT(*)
      FROM mobile_notification_deliveries GROUP BY status, reason;
    SELECT category, COUNT(*)
      FROM mobile_notification_events GROUP BY category;

Expected behavior:

- `queued`/`retry` should drain after `next_attempt_at` plus at most one sweep period.
- provider configuration outages preserve delivery attempts and retry every 15 minutes.
- an event stops being deliverable seven days after snapshot; the next claim records
  `skipped/event_expired` rather than sending stale business activity.
- APNs `5xx` retries wait at least 15 minutes; `429` honors `Retry-After`;
  `TooManyProviderTokenUpdates` waits at least 20 minutes.
- `BadDeviceToken`, `DeviceTokenNotForTopic`, `Unregistered`, or `410` deactivates
  only the compared token version and erases its ciphertext.
- delivery is at-least-once. Stable event/APNs/collapse IDs reduce visible duplicates,
  but a crash after APNs accepts and before SQLite commits can still duplicate one.
- billing-locked retained tenants still receive session-expiry/token-erasure and
  retention cleanup, but the scheduler does not enqueue delivery work for them.
- inactive registration metadata and expired notification diagnostics are removed
  after `MISE_APNS_RETENTION_DAYS`; tenant deletion removes the tenant-local database.
  Support exports must omit installation/token fingerprints and ciphertext.

## 7. Incident and rollback actions

- **Stop sends without taking Mise down:** remove the APNs provider-key source and
  restart. Delivery remains durable with attempts preserved; registration/read APIs
  continue if topic and token-storage encryption are configured.
- **Compromised `.p8`:** revoke it in Apple Developer, create a new key, update key
  ID/private key together, and restart to clear the provider-token cache. Device
  registration does not need to change.
- **Compromised/lost token-storage key:** this version has no online key ring. In
  every tenant transaction, deactivate active devices, null ciphertext, and bump
  token/revision before installing a new random key. Devices re-register on their
  next authorized launch. Never retain undecryptable ciphertext as active.
- **Topic/environment mismatch:** stop distribution, align bundle ID, provisioning,
  app xcconfig, and backend configuration. Do not accept a caller-selected topic or
  silently send a sandbox token to production.
- **Payload/privacy incident:** disable the provider immediately, preserve the
  raw-token/ciphertext-free event/delivery audit rows (delivery rows still contain a
  keyed token fingerprint/version), and treat any client or credential data in an
  alert as a release blocker. The fixed templates and exact payload decoder are an
  intentional containment boundary.

Before App Store submission, reconcile the privacy manifest, App Store Connect
answers, published privacy policy, and observed traffic. The checked-in manifest
declares linked Device ID, User ID, Name, Email Address, Phone Number, Other User
Content, and Other Data Types for app functionality; none are used for tracking. It
also declares the checked-in required-reason API uses. Final acceptance requires the
simulator suite, signed archive/privacy report inspection, real-device sandbox
delivery, and TestFlight production delivery.
