# Mise iOS

The checked-in project is generated with XcodeGen so project-file churn does not
obscure source reviews.

## Requirements

- macOS with the current stable Xcode capable of targeting iOS 17
- XcodeGen 2.45.4 or newer
- an iOS 17+ simulator or device

## Generate and run

1. Set the hosted platform root in `Config/Debug.xcconfig`. The current value is a
   non-production placeholder. A hosted slug such as `north-star` resolves beneath
   this root; users enter a full origin for custom or self-hosted servers.
2. If the bundle identifier or signing team differs, update `project.yml`.
3. From this directory, run:

       xcodegen generate
       open Mise.xcodeproj

4. Select the Mise scheme and an iOS 17+ destination.
5. Run the MiseTests test plan from Xcode or:

       xcodebuild test \
         -project Mise.xcodeproj \
         -scheme Mise \
         -destination 'platform=iOS Simulator,name=iPhone 16'

The core foundation intentionally uses URLSession, Security, LocalAuthentication,
SwiftUI, and Observation with no third-party runtime dependency. Swift Charts is
part of SwiftUI and can be added to the dashboard feature. Evaluate Kingfisher when
the gallery UI lands; the API client already supports authenticated media requests,
and avoiding it in the foundation keeps auth/session behavior auditable.

## Configuration notes

- Release configuration refuses a non-HTTPS server URL.
- `MiseServerBaseURL` is the hosted platform root and should ultimately be supplied
  by CI per environment. It is not a tenant origin.
- Milestone 1 implements tenant discovery, owner password sign-in, exact-capability
  shared client access, Keychain-backed sessions, and biometric re-entry. Custom
  and self-hosted origins are entered in the app and remain isolated per origin.
- Milestone 2 adds the cache-first owner dashboard, clients, projects, gallery
  manifests, and upcoming-booking agenda with adaptive iPhone/iPad navigation.
- Milestone 3 adds exact-capability client delivery: an authenticated native gallery
  grid/lightbox, visitor favorites, threaded video review notes, protected explicit
  downloads, and cache-first portal/workspace/document summaries. Signing and
  payment stay on same-origin studio webpages.
- Milestone 4A adds audited owner mutations for clients, projects, and tasks.
  Every command uses a session-bound idempotency key; updates and deletes require
  an `ETag`/`If-Match` version. Cached reads remain available offline, while writes
  require the network and preserve form input when the server reports a conflict.
  Money and native legal mutations remain deferred.
- Milestone 4B adds owner booking cancellation/rescheduling and exact-capability
  client proposal decisions. These commands use strong versions and stable retry
  keys; notification/workflow effects are persisted to the tenant job queue.
  Booking creation remains on the public scheduler until a dedicated mobile
  booking credential can preserve its anti-abuse controls.
- Gallery media uses the active session's single rotating authenticator. Server
  media URLs are accepted only when their origin and exact capability path match
  the active workspace; redirects are rejected and bearer tokens never enter URLs.
- Do not add access tokens, refresh tokens, PINs, Stripe secrets, or APNs keys to
  xcconfig files.

See `../docs/IOS-ARCHITECTURE.md` and `../docs/IOS-API-V1.md` for the product and
backend plan.
