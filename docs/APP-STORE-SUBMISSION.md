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

**Provisioning — `scripts/seed_demo_tenant.py`** (run once against the hosted
control DB on staging/prod; idempotent, touches only the demo tenant):

```sh
MISE_SAAS_MODE=true MISE_SAAS_ROOT_DOMAIN=<root-domain> \
MISE_SAAS_CONTROL_DB_PATH=/data/saas-control.db \
MISE_SAAS_TENANT_DATA_DIR=/data/tenants \
MISE_SECRET_KEY=... MISE_ADMIN_PASSWORD=... \
MISE_DEMO_TENANT_PRESET=wedding \
MISE_DEMO_TENANT_PASSWORD='<reviewer sign-in password>' \
python -m scripts.seed_demo_tenant
```

It creates (or safely reuses) a reviewer tenant identified by
`signup_source='reviewer-demo'` — and **refuses** a slug that already belongs to a
real studio, so it can never reactivate one. It grants non-expiring access by
keeping the tenant `trialing` with a far-future `trial_ends_at`: full access
(`tenant_has_access` = `trial_ends_at >= now`) but counted as trial *pipeline*,
**never as paid MRR** (only `active` tenants book `active_mrr_cents`). It seeds a
realistic studio from an `app/saas_demo.py` preset (`MISE_DEMO_TENANT_PRESET` is
**required** to be `wedding` or `fnb` — `neutral` has no seed yet, see T10) plus an
owner task and a freshly-dated upcoming booking, refreshed every run so the demo
never decays past a stale booking date. Re-running rotates the advertised
credentials and converges the studio without duplicating data.

- Review notes to include: the studio address to enter at sign-in
  (`<slug>.<root-domain>` or its full URL), owner email
  (`MISE_DEMO_TENANT_EMAIL`, default `reviewer@demo.mise.local`) + the password
  you set, and — to show the client experience — one gallery guest credential
  (the seeded gallery's slug + PIN, read from the tenant DB after seeding).
- Note for reviewers: subscriptions are purchased on the web, not in the app;
  the app sells nothing (ADR 0070).
- Pick `MISE_DEMO_TENANT_PRESET` to match the T10 niche decision. The script
  requires an explicit `wedding` or `fnb` and refuses `neutral`/unset, so a demo
  is never seeded with a niche before the decision is recorded.

**Still open before T3 can be called complete** (tracked in the PR): representative
gallery photos are uploaded manually at demo-prep time (a seed can't invent real
images); and a hosted owner-login acceptance test through `/api/v1` is the
remaining automated gate beyond these unit/direct-DB tests. Manual TestFlight
sign-in remains the deployment-time proof.

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
