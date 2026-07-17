# App Store game plan — Mise as a revenue-generating micro-SaaS

**Date:** 2026-07-17 · **Owner:** Kevin · **Decision base:** ADR 0047 (product
model), ADR 0066/0067 (native auth + client delivery), ADR 0070 (distribution)
**Audience:** the agent fleet (Claude/Opus/Sonnet, Codex, Cursor, …) executing under
`AGENTS.md`. Written for cold pickup: every item carries done criteria, verification,
risk class, and a lane. Grounded in the 2026-07-17 three-lane audit (iOS App Store
readiness · tenant API/funnel · billing state); evidence cited inline as `file:line`.

> **Execution has moved to [`CONDUCTOR-PLAN.md`](CONDUCTOR-PLAN.md)** (same evening):
> it carries the live board, the binding post-incident process rules, and the
> remaining work as tickets T1–T10. This document stays as the strategy/evidence
> layer; do not add new work items here.

## North star

Mise ships as a **hosted micro-SaaS at $20/month per studio** (instance-per-customer,
ADR 0047) with a **free multi-tenant companion app on the App Store** (ADR 0070).
Revenue is the web Stripe subscription; the app is the acquisition and retention
surface. The operator's own studio is tenant #1, not the product.

Definition of launch: a stranger can find the marketing site, start a trial, get
their own studio subdomain, download the Mise app, sign into their studio, and run
their photography business from it — and their card is charged $20/month by Stripe
until they cancel. Nothing in that sentence requires a human operator in the loop.

## Standing rules (non-negotiable, from AGENTS.md + CLAUDE.md)

- **Green-light** work (tests, docs, UI, non-money features, tooling) may ship
  straight to `main` with all gates green. **Red-light** work (money path, schema/
  migrations, deploy, security, contracts/legal) goes on a `<agent>/<slug>` branch
  as a PR using the repo template (What/Why/Verified/Rollback/Risk) — **a human
  merges**. When unsure, treat it as red.
- Gates: `pytest -m unit` · full e2e `pytest -m "not unit"` (throwaway
  `MISE_DATA_DIR`) · `ruff check` + `ruff format --check`. iOS verifies **only**
  via the CI `build-test` gate (XcodeGen + `xcodebuild test`) — never claim Swift
  works from local reasoning. (The gate exists and runs on `ios/**` PRs; any doc
  line claiming otherwise predates it.)
- §11.4 stands everywhere: model proposes, human approves. Nothing auto-sends,
  auto-charges, auto-publishes.
- This repo is the **sandbox**; `Ayyitskevin/kleephotography` is the live deploy.
  Nothing here reaches customers until the launch phases below deliberately do it.

## Current verified state (2026-07-17 audit)

**The hard product engineering is done; the App Store packaging layer has not been
started, and launch is blocked on ops, not code.**

What the audit **confirmed working** (merged to `main`):

- `/api/v1` is genuinely hosted-ready: host-first tenant resolution runs before the
  mount (`app/saas.py:1546` middleware; `app/main.py:131`), sessions are bound to
  tenant + origin, unknown/billing-locked tenants get problem+json 404/402, and
  hosted tests cover discovery, login, cross-tenant replay, and billing-locked
  recovery. `GET /api/v1/tenant` returns name/accent/origin/tz/currency/auth-methods
  (`app/mobile_api.py:286`).
- The iOS app is **already multi-tenant at sign-in**: user-entered studio address
  (full URL or hosted slug) via `WorkspaceAddressParser`
  (`ios/…/AuthenticationParsing.swift:76`), per-origin Keychain + cache isolation
  (`AppEnvironment.swift:17`), https enforced in code with **zero ATS exceptions**,
  Face ID lock, offline cache-first snapshots, and the 402
  `tenant.subscription_required` problem already modeled. No personal-studio
  hardcoding beyond the `com.ayyitskevin` bundle prefix.
- Native command surface: task check-off, booking cancel, atomic reschedule backend
  with session-bound `Idempotency-Key` replays (migration 082) and the flag-gated
  durable side-effects workflow (migration 083, `MISE_BOOKING_WORKFLOW_ENABLED=false`),
  source-aware slot feed. Native reschedule UI is in recovery PR **#165**.
- The **revenue path is code-complete**: invite-gated signup, 14-day trial with
  reminder/win-back sweeps, $20/mo Stripe checkout + signature-verified exactly-once
  webhooks, 10-day dunning grace, self-serve cancel/delete (tombstone) and
  full-studio export, stateless password reset, trust pages, fail-closed tenant BYO
  Stripe keys, backup sidecar + restore-drill docs, Telegram ops alerting
  (ADRs 0047–0060; `docs/LAUNCH-PLAYBOOK.md:3` — "everything code-side is done and
  merged; what remains needs real accounts and a real box").

What the audit found **missing or gapped** (every Phase item below traces to one):

| # | Gap | Evidence |
|---|-----|----------|
| G1 | **No asset catalog at all** — no `Assets.xcassets`, no AppIcon (incl. 1024pt) → App Store upload validation fails | `find ios/` zero `.xcassets`; `ios/project.yml:29` |
| G2 | **No `PrivacyInfo.xcprivacy`** despite required-reason API use (UserDefaults `AuthenticationCoordinator.swift:565,594`; file timestamps `TenantJSONCache.swift:19,141`) → ITMS-91053 rejection | `find ios/` zero `.xcprivacy` |
| G3 | Release platform root is the placeholder `https://mise.example` ("Replace before archiving") so slug shorthand resolves to a dead host | `ios/Config/Release.xcconfig:1` |
| G4 | No signing team / final bundle-id decision; version stuck 1.0 (1) | `ios/project.yml:47-49`; `docs/IOS-ARCHITECTURE.md:335` |
| G5 | No in-app account-deletion path or link (web `/admin/delete-studio` exists, ADR 0051) | grep ios/ — only HTTP DELETE verbs |
| G6 | No reviewer/demo studio: `/demo` is a static tour; demo seeding (`app/saas_demo.py:47`) is web-admin-only; invite gate 403s a reviewer; trials 402 after 14 days mid-review | `app/saas.py:1658`; `app/saas.py:1748` |
| G7 | No app→signup funnel: signup is a web-only form POST (`app/saas.py:1724`), no "start a studio" link in the app, no signup/marketing URL in the tenant descriptor | tenant-api audit |
| G8 | API never returns a billing-management URL; an app link-out would have to hardcode `/admin/billing` (a CSRF-gated web-cookie surface) | `app/mobile_api.py:113` |
| G9 | `ITSAppUsesNonExemptEncryption` unanswered; `fatalError` launch path makes config mistakes fatal in review builds | ios-appstore audit |
| G10 | No hosted happy-path feature test (dashboard/galleries 200 on a trialing tenant subdomain) — hosted tests stop at auth/recovery | tenant-api audit |
| G11 | Launch Playbook Stages 3.1–5 all unchecked: domain/VPS/Stripe/B2/Telegram accounts, deploy, `.env` fill, money rehearsal, backup+restore drill, counsel skim of `/terms` `/privacy` | `docs/LAUNCH-PLAYBOOK.md:21-153` |

## Phases

Each item: **[lane]** = Opus (judgment) / Sonnet (mechanical, fully specified) /
Kevin (human-only). Risk: 🟢 green-light · 🔴 red-light (PR + human merge).

### Phase 1 — Close out the native command surface

1. **Land PR #165 (native reschedule recovery).** [Kevin merges; fleet fixes CI] 🔴
   Done: `build-test` green against current `main`; merged. Verify: the recovered
   test suite passes in CI; no capability flag flipped.
2. **Durable-workflow activation review.** [Opus + Kevin] 🔴 The
   `MISE_BOOKING_WORKFLOW_ENABLED` gate holds three linked reviews: migration 083
   effects/outbox correctness, worker crash-recovery, client calendar (ICS/UID)
   delivery — plus the two held #164 security questions (SessionAuthenticator
   token-refresh session-ID preservation; sign-out vs late cache-write race).
   Done: written approval (ADR or PR) + an e2e test proving reschedule effects
   dispatch exactly once across a simulated worker crash. Until then: flag stays
   `false`.

### Phase 2 — Funnel + polish (small, high-leverage; mostly 🟢)

3. **Tenant descriptor funnel fields.** [Sonnet] 🟢 (contract-adjacent — treat as
   🔴 if reviewers disagree). Extend `GET /api/v1/tenant` and/or the 402 problem
   with optional `signup_url` and `manage_billing_url` (external web links), so the
   app never hardcodes admin paths (G7/G8). Additive/optional only — no breaking
   contract change; update `docs/IOS-API-V1.md` + OpenAPI guard test.
   Done: fields serialized when hosted config present. Verify: contract tests.
4. **"Start a studio" link on the workspace-entry screen.** [Sonnet] 🟢 Opens the
   platform `/pricing` (from item 3's `signup_url` or the build-time platform root)
   in Safari (G7). Done: fresh install shows the path to becoming a customer.
   Verify: XCTest for link construction; CI.
5. **Manage-billing link-out on the 402 screen.** [Sonnet] 🟢 App maps
   `tenant.subscription_required` to a clear owner-facing state with a Safari
   link-out (item 3's URL) (G8). Verify: XCTest with stubbed 402.
6. **Hosted happy-path e2e test.** [Sonnet] 🟢 Trialing tenant → login on its
   subdomain → `/api/v1/dashboard` + `/galleries` return 200 tenant-scoped (G10).
   Verify: new test green in the full suite.

### Phase 3 — App Store submission pack

7. **Asset catalog + AppIcon.** [Kevin supplies/approves art; Sonnet wires] 🟢
   `ios/Mise/Assets.xcassets` with full AppIcon set incl. 1024pt marketing icon,
   registered in `project.yml` (G1). Done: CI build embeds the icon set. Verify:
   `build-test` green; archive validation on a Mac (Kevin).
8. **Privacy manifest + labels.** [Sonnet] 🟢 `PrivacyInfo.xcprivacy` declaring
   `NSPrivacyAccessedAPICategoryUserDefaults` (CA92.1) + file-timestamp category
   (C617.1) and collected-data types (account identifiers, user content, device
   installation ID); draft the App Store privacy-label answers in
   `docs/APP-STORE-SUBMISSION.md` (G2). Done: manifest in project; answers match
   actual behavior. Verify: CI; doc review against the audit's API inventory.
9. **Export compliance + launch hardening.** [Sonnet] 🟢 Add
   `ITSAppUsesNonExemptEncryption=false` to Info.plist properties (standard HTTPS
   exemption); replace the `fatalError` config launch path with a recoverable
   error screen (G9). Verify: XCTest for the misconfig path; CI.
10. **Account-deletion affordance.** [Sonnet] 🟢 Owner-settings row linking to the
    tenant's `/admin/delete-studio` (and export) web flow (G5; Guideline 5.1.1(v)
    safety for a login-only app). Done: reachable in ≤2 taps signed-in. Verify:
    XCTest for menu entry + URL construction.
11. **App identity decisions.** [Kevin] Final app name, bundle id (currently
    `com.ayyitskevin.mise`), `DEVELOPMENT_TEAM` in `project.yml`, version bump
    (G4). Then one archive on a Mac to validate signing end-to-end. Fleet
    executes the `project.yml` edits once decided.
12. **Reviewer demo studio.** [Opus spec, Kevin provisions] 🔴 (touches hosted
    provisioning). A comped long-lived reviewer tenant (`plan_status='active'`,
    e.g. `review.<root>`) seeded with both `saas_demo` presets; owner + gallery/
    portal credentials documented in `docs/APP-STORE-SUBMISSION.md` review notes
    (G6). Done: fresh reviewer signs in and sees a populated studio that never
    trial-expires. Verify: scripted seed run against staging.
13. **Guideline verification pass.** [Opus] 🟢 docs-only. Verify CURRENT App Store
    Review Guidelines for: web-subscription companion apps (3.1.x multiplatform
    services), account deletion (5.1.1(v)), demo access (2.1), privacy labels.
    Cite guideline text as of submission week — never from memory (ADR 0070 §3).
    Done: findings appended to `docs/APP-STORE-SUBMISSION.md` with citations.

### Phase 4 — Hosted launch runway (ops, human-gated; G11)

14. **Stage 3.1 operator accounts.** [Kevin, ~1 hour] Domain, Cloudflare, VPS,
    Stripe activation, Backblaze B2, Gmail app password, Telegram bot. The sole
    prerequisite chain for everything below (`docs/LAUNCH-PLAYBOOK.md`).
15. **Deploy staging hosted mode.** [Kevin + Opus] 🔴 Compose + Caddy/Cloudflare
    per `docs/SAAS-DEPLOYMENT.md`; wildcard TLS; `.env` fill; preflight READY.
    Done: `/api/v1/tenant` answers on a tenant subdomain over TLS (ATS-clean).
    Verify: `curl` + app sign-in from TestFlight build. Set the real platform
    root in `Release.xcconfig`/CI at this point (G3).
16. **Money rehearsal + drills.** [Kevin + fleet] 🔴 The 8-step Stripe test-mode
    rehearsal, one backup + restore drill on the real box, counsel skim of
    `/terms` + `/privacy`, uptime monitor + Telegram alerts armed. Then live keys.
17. **TestFlight beta.** [Kevin] Internal → external against staging. Done:
    external build approved; feedback loop documented.

### Phase 5 — Submission + post-launch

18. **App Store submission.** [Kevin] Submit with the demo credentials + notes
    from items 12/13. Done: approved and live.
19. **Post-launch ops.** [fleet] Crash/ANR monitoring decision (own ADR — no
    third-party SDK without it), support loop (`docs/SUPPORT-PLAYBOOK.md`),
    release cadence, metadata iteration, `.trash` hard-purge job, Stripe Connect
    onboarding (ADR 0049 follow-up), control-DB key encryption at rest.

## Sequencing

- Phases 1–3 are **fleet-executable today**, in parallel, with no hosted deploy:
  everything verifies via the test suite + the iOS CI gate.
- Phase 2 item 3 precedes items 4–5 (they consume its URLs, though both can fall
  back to the build-time platform root).
- Phase 4 is strictly ordered and human-gated; item 14 unblocks all of it.
- Phase 5 last. The critical path to revenue is: **14 → 15 → 16** (server, ~a day
  of ops) — the App Store app rides that runway but does not gate it.

## Out of scope for v1 (each needs its own ADR to enter)

Apple IAP · push notifications · per-studio white-label binaries · custom tenant
domains (Cloudflare Custom Hostnames, ADR 0059 deferral) · offline write queue ·
Android.
