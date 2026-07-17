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
- Milestone 3 adds the client experience (Home / Gallery / Documents /
  Bookings) for the four shared-access principals, the shared gallery grid +
  lightbox with gallery-guest favoriting, bearer-authenticated media loading,
  and the design-handoff tokens (`MiseDesign`). Display type uses the system
  serif design as the Newsreader stand-in; bundling the handoff's
  Newsreader/Archivo webfonts is a pending asset/licensing decision.
- Milestone 4a has merged owner `studio:write` commands: dashboard task
  check-off with session-local Undo, plus confirmed booking cancellation from
  the owner agenda. Task check-off is optimistic and naturally idempotent;
  booking cancellation remains visible until the server confirms it because a
  real transition starts best-effort client-notification and calendar cleanup.
  Native booking rescheduling is implemented as a capability-gated flow: its
  destination must come from the source-aware slot feed, the exact session-bound
  request and idempotency key are persisted before POST, ambiguous outcomes can
  only replay that saved command, and provider-workflow status remains visible
  after the booking moves. The reschedule backend and slot feed are on `main`;
  the server capability stays default-off (`MISE_BOOKING_WORKFLOW_ENABLED`) until
  the durable-workflow and session-identity reviews are approved.
- Do not add access tokens, refresh tokens, PINs, Stripe secrets, or APNs keys to
  xcconfig files.

See `../docs/IOS-ARCHITECTURE.md` and `../docs/IOS-API-V1.md` for the product and
backend plan.
