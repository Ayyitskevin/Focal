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
  installation identity, `AuthenticationCoordinator.swift`); file timestamps →
  `C617.1` (tenant JSON cache freshness, `TenantJSONCache.swift`).

### App Store privacy-label answers (draft — mirror into Connect)

Data collected, all **App Functionality · linked to identity · not used for
tracking**, sent only to the user's own studio server:

| Connect category | What it actually is |
|---|---|
| Contact info → Email address | owner sign-in email |
| Contact info → Name | studio display name / owner name on the account |
| Identifiers → User ID | the account/principal identity |
| Identifiers → Device ID | per-install random UUID for the owner's device/session list (not IDFA/IDFV) |

Explicitly **not collected**: location, contacts, browsing history, purchase
history, health, financial data, photos *from the device* (the app has no camera
or photo-library access — media is viewed over the network from the studio
server; gallery guests' favorite selections are studio business data on the
studio's server).

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

## Reviewer access (game-plan item 12 — required before submission)

App Review will demand working credentials for a sign-in-only app (Guideline 2.1).
Plan (not yet provisioned):

- A comped, long-lived reviewer tenant on the hosted platform
  (`plan_status='active'`, e.g. `review.<root-domain>`) seeded with both
  `app/saas_demo.py` presets, so it never trial-expires mid-review.
- Review notes to include: the studio address to enter at sign-in
  (`review.<root-domain>` or its full URL), owner email + password, and one
  gallery/portal guest credential to show the client experience.
- Note for reviewers: subscriptions are purchased on the web, not in the app;
  the app sells nothing (ADR 0070).

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
