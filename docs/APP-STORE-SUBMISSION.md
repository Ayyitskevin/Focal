# App Store submission pack

Working record for shipping the Mise iOS companion app (ADR 0070,
`docs/APP-STORE-GAMEPLAN.md` Phase 3/5). Everything App Store Connect will ask
for lives here so submission is an execution day, not a scavenger hunt. Keep this
file, `ios/Mise/PrivacyInfo.xcprivacy`, and the Connect privacy labels in
lockstep — they describe the same facts.

## App identity (Kevin decisions — game-plan item 11)

| Field | Current | Status |
|---|---|---|
| App name | Mise | placeholder — confirm availability in Connect |
| Bundle id | `com.ayyitskevin.mise` | confirm or replace before first archive |
| `DEVELOPMENT_TEAM` | unset | set in `ios/project.yml` once the Apple team exists |
| Version | 1.0 (1) | fine for first submission |
| App icon | **generated placeholder** (terracotta aperture, `Assets.xcassets/AppIcon.appiconset`) | replace with final art before submission; placeholder exists so archive validation passes |
| Category | Business (primary), Photo & Video (secondary) | proposal |

## Privacy

### Privacy manifest (shipped: `ios/Mise/PrivacyInfo.xcprivacy`)

- **Tracking:** none; no tracking domains.
- **Required-reason APIs:** UserDefaults → `CA92.1` (last-workspace origin +
  installation identity, `AuthenticationCoordinator.swift`). File-timestamp
  reason `C617.1` was **removed** 2026-07-20 — `TenantJSONCache` uses app-owned
  `storedAt` JSON fields, not file-timestamp APIs (see auditor + #179 memo).

### App Store privacy-label answers (draft — mirror into Connect)

Data collected, all **App Functionality · linked to identity · not used for
tracking**, sent only to the user's own studio server. Each row is traced to code
at `d34cc66` (#179):

| Connect category | Manifest key | What it actually is | Source |
|---|---|---|---|
| Contact info → Email address | `EmailAddress` | owner sign-in email | `StudioLoginRequest` → `POST /api/v1/auth/studio/login` (`AuthenticationCoordinator.swift:193`); `app/mobile_auth.py` |
| Contact info → Name | `Name` | **no name is typed into the app today.** Declared because the device name sent at sign-in (`UIDevice.current.name`, `AuthenticationCoordinator.swift:622`) usually contains the owner's name and the server stores it (`api_sessions.device_name`, `app/mobile_auth.py:293-315`). The studio/owner name the app displays is returned by the server, not sent. | decision A below |
| Identifiers → User ID | `UserID` | account/principal id, session id, and the gallery-visitor id a guest session carries | `api_sessions` (`migrations/081_mobile_api_sessions.sql`), `gallery_visitor_id` |
| Identifiers → Device ID | `DeviceID` | per-install random UUID (stored hashed: `installation_id_hash`), device platform/app version; **and the client IP**, kept up to 24 h for sign-in lockout (`pin_attempts`, `app/security.py:145-150`). Not IDFA/IDFV. | decision B below |
| Usage data → Product interaction | `ProductInteraction` | retained session activity (`created_at`, `last_seen_at`, `app/mobile_auth.py:293-315,699-704,813-816`); owner task check-off plus audit row (`app/mobile_owner_api.py:886-900`); gallery favorites (`app/mobile_client_api.py:226-262`); booking cancel, reschedule and workflow retry (`app/mobile_gallery_calendar_api.py:1496,1556,1614`) | Apple: "app launches, taps, clicks … other information about how the user interacts with the app" |
| User content → Other user content | `OtherUserContent` | owner-chosen reschedule start time/time zone and guest favorite selections, retained as studio business data | decision C below (**Kevin to confirm**) |

**Decisions made here, each for Kevin's confirmation:**

- **A. Name — kept, with a narrower reason.** The issue said the "studio display name /
  owner name" wording did not match what is sent; it did not. The native app sends no
  typed name. Keeping `Name` is the conservative call because the retained device name
  usually contains it. If you would rather drop `Name`, send a generic device name
  (for example the model) instead of `UIDevice.current.name` and remove the entry.
- **B. Client IP — two options; Kevin chooses.**
  Apple: declare IP "based on how you use" it ("such as precise location, coarse
  location, device ID, or diagnostics"); an IP "sent on a server call and not retained"
  needs no disclosure, and data kept "longer than the time it takes" to service the
  request does.
  - *Option 1 (drafted): cover it under Device ID / App Functionality.* The app keeps the
    client IP of failed sign-ins for 24 h as a lockout counter (`pin_attempts`,
    `app/security.py:145-150`), used only to throttle repeated failures, never for
    location or analytics. It is retained past the request, so Apple's "not retained"
    exemption does not apply; Device ID / App Functionality is the closest honest label,
    and the manifest already declares it.
  - *Option 2: say nothing extra about IP.* Defensible only if you treat the counter as
    transient. It fails Apple's reading as soon as the row outlives the request, and it
    does, for 24 h. Not recommended.
  - **Kevin to check:** how long the host's access and server logs keep client IPs. The
    app also writes failed-auth lines to the server log (`app/security.py:151`), and an
    ordinary access log may keep every caller's IP. That retention is set by the host,
    is **not verified here**, and, if long, still falls under Option 1's label.
- **C. Other User Content — declared.** Apple says free-form fields are "Other User
  Content" and you need not disclose everything a user may type, but the owner chooses a
  reschedule time and a guest chooses favorites, and both are retained. Declaring is the
  safer error; dropping it is defensible if you read booking times as plain Product
  Interaction. Not declared: no free-text note, message or upload is sent by the app.

**Watch items (code exists, no UI caller at `d34cc66`):** `createBooking`
(`BookingCreateRequest`: name, email, phone, notes), `signContract`
(`ContractSignRequest.signerName`), proposal accept/decline, and invoice checkout are
defined in `MiseEndpoints.swift` but no screen calls them. If a screen starts to,
add Name, Email, Phone Number and Other User Content (notes, signature) and revisit
Purchases before submitting.

Explicitly **not collected**: location, contacts, browsing history, health,
financial/payment card data (Stripe runs on the web), photos *from the device* (the app
has no camera or photo-library access — media is viewed over the network from the
studio server).

### Still to do by hand (Mac / App Store Connect)

1. Archive on the signing Mac, run **Product → Generate Privacy Report**, and compare it
   with this table and with the Connect answers (issue finding 4); the report is the
   evidence, not the CI build.
2. Enter the table above in Connect → App Privacy; "used for tracking" = No everywhere.
3. Confirm decisions A, C with Kevin before entering them.

### Export compliance

`ITSAppUsesNonExemptEncryption=false` is set in `ios/project.yml` — the app uses
only standard HTTPS/ATS transport security (exempt).

## Account lifecycle (Guideline 5.1.1(v))

The app is sign-in only (accounts are created on the web). A signed-in owner
reaches **Export studio data** and **Delete studio account** from the account
menu (`OwnerCompanionView` → `StudioAccountLinks`), which open the
server-authoritative, password-confirmed web flows (ADR 0051:
`/admin/export-studio`, `/admin/delete-studio`). Deletion cancels billing,
tombstones the slug, and trash-parks data per ADR 0051.

## Reviewer access (Conductor T3; game-plan item 12 is superseded)

App Review will demand working credentials for a sign-in-only app (Guideline 2.1).

> **BLOCKED — do not run `scripts/seed_demo_tenant.py` against any hosted state.**
> The command is intentionally disabled and exits before reading configuration or
> opening a database. [Issue #185](https://github.com/Ayyitskevin/Focal/issues/185)
> records the proven unsafe behavior in the retired implementation and owns the
> replacement contract.

No App Review credentials are currently provisioned. T3 remains incomplete, and
App Store submission remains on hold. The replacement must use durable,
operator-only demo identity rather than public attribution; exclude the demo from
billing, growth, lifecycle, and checkout paths; preserve manually created and
workflow-referenced records; avoid rotating unchanged credentials; and pass a
hosted owner-login plus populated `/api/v1` acceptance test. Any control-database,
billing, or authentication changes require Kevin's explicit design and merge
approval.

Once that replacement is reviewed and merged, this section must document the exact
provisioning command, studio address, owner credentials workflow, gallery guest
credential workflow, expiry/billing isolation, reset procedure, and manual
TestFlight evidence. Until then, `/demo` is only the unauthenticated static product
tour; it is not a reviewer account.

## Guideline verification (game-plan item 13 — do at submission week)

Verify against the **current** App Store Review Guidelines, citing text — never
memory: 3.1.x multiplatform-services treatment of a free companion app whose
subscription is sold on the web · 5.1.1(v) account deletion · 2.1 demo access ·
privacy-label accuracy. Append findings + citations here.

## Archive checklist (Mac required)

1. `cd ios && xcodegen generate`
2. Set `MISE_SERVER_BASE_URL` (real hosted platform root) — `Release.xcconfig`
   still ships the `https://mise.example` placeholder (game-plan G3); CI or the
   archiving machine must override it before any distributed build.
3. Xcode: Product → Archive → validate. First run surfaces signing (team,
   capabilities) and asset gaps.
4. Distribute to TestFlight internal, then external (game-plan item 17).
