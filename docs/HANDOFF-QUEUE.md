# Execution queue — from MISE-REVIEW.md + IOS-UPGRADE.md (2026-07-12)

Ordered task list for cold pickup. Every task: done criteria, real
verification, risk tag. **Red-light** = money path, auth/CSRF, migrations,
contracts, or deploy per CLAUDE.md → reviewed draft PR only, never
self-applied. Sources: `docs/MISE-REVIEW.md` (review), `docs/IOS-UPGRADE.md`
(plan), `docs/SESSION-HANDOFF.md` (topology detail).

## Opus lane — judgment-heavy, decide before coding

### O1. AI-sidecar consolidation plan (the architecture-directive item)
- **What:** Review found every live AI capability calls a separate
  self-hosted sidecar (Argus vision, Odysseus captions, Dionysus packs),
  with challenger endpoints on the operator's homelab — unacceptable for a
  hosted product per the architecture directive. Produce the consolidation
  design: per capability, either move the logic in-process inside Mise's
  deployable unit or replace the sidecar hop with a direct hosted-API call
  (vendor dependency, like Stripe). Use the existing seams: the provider
  registry (`app/providers/registry.py`) is the strangler switch;
  `docs/MISE-CONSOLIDATION-ROADMAP.md` sketches phases; the
  `active_vision_provider` interlock already guards production cutover.
  Decide order (suggest: Odysseus captions first — single POST, simplest;
  Argus last — it owns trigger→callback→writeback), the shadow/parity
  window per capability, and what happens to `MISE_*_URL/_TOKEN` config.
- **Done:** an ADR (design + per-capability cutover/rollback plan) merged;
  no code required to close this item.
- **Verify:** ADR names, for each capability, the target topology
  (in-process vs hosted API), the flag that flips it, the rollback (one
  env var, strangler rule), and the §11.4 review surface it keeps.
- **Risk:** red-light adjacent (contracts, deploy) → draft PR, human merge.

### O2. Commercial-spine mobile API design (IOS-UPGRADE step 3)
- **What:** Design the read-only `/api/v1` surface for AR chase assist,
  closeout-readiness, company next-action ranking, and the Studio Activity
  commercial queue. Judgment: which admin panels map to which DTOs, what's
  summarized vs paginated, where "mark chased"-style assists fall (M4
  mutation rules: idempotency key, server-authoritative transitions).
- **Done:** contract section added to `docs/IOS-API-V1.md` (DTO shapes,
  routes, principals — owner-only) + task breakdown appended here for the
  Sonnet lane.
- **Verify:** contract review against the actual admin queries it mirrors
  (`app/admin/` financials/studio modules); no new authority invented; no
  auto-send/auto-charge anywhere.
- **Risk:** normal (design doc). Implementation later inherits red-light
  where it touches AR/invoice data paths.

### O3. iOS distribution decision (IOS-UPGRADE step 6 — blocked on Kevin)
- **What:** Resolve the open product question: single-tenant
  kleephotography app vs hosted multi-tenant awareness. Then scope
  branding, `MiseServerBaseURL` provisioning, TestFlight/App Store path.
- **Done:** decision recorded (ADR or IOS-ARCHITECTURE §11 update) with
  Kevin's explicit answer; queue items appended for the chosen path.
- **Verify:** decision doc names the distribution channel and its review
  requirements; no code assumption collapsed before the decision.
- **Risk:** normal (product decision); blocks nothing else in this queue.

### O4. Sidecar credential hygiene review (security flag from review §6)
- **What:** Investigate the three flags: static long-lived sidecar bearer
  tokens with no rotation story; plain-`http://` challenger endpoints
  carrying client-media derivatives; post-075 stale homelab config in
  `.env.example`. Decide rotation policy and transport requirements —
  partially subsumed by O1 (consolidation removes most sidecar auth).
- **Done:** findings + remediation plan handed to a security review;
  anything auth-touching ships as red-light PRs.
- **Verify:** each flag either remediated, scheduled, or explicitly
  accepted with rationale.
- **Risk:** **red-light** (auth) → reviewed draft PR only.

## Sonnet lane — mechanical, fully specified

### S1. iOS CI job (IOS-UPGRADE step 1 — do first in this lane)
- **What:** Add a `macos-latest` job to `.github/workflows/ci.yml`,
  triggered on `ios/**` paths: install XcodeGen (brew), `xcodegen
  generate` in `ios/`, `xcodebuild test -project Mise.xcodeproj -scheme
  Mise -destination 'platform=iOS Simulator,name=iPhone 16'`.
- **Done:** job green on a PR that touches `ios/**`; failures block merge.
- **Verify:** push a trivial `ios/` whitespace PR; the job runs, builds,
  and executes all 12 MiseTests files (fix any compile errors it surfaces
  in the M3 test files — expected, they've never run in CI).
- **Risk:** normal (CI config; touches deploy pipeline file → have Kevin
  merge, treat as red-light-lite).

### S2. Doc accuracy fixes (review §1/§3, cheap)
- **What:** CLAUDE.md — replace the five-capability list with the real
  three (vision/content/products-dormant), note Plutus+Mnemosyne
  decommissioned (migration 075); fix directory map `templates/ (admin/* +
  client/*)` → `admin/ public/ saas/ site/`. Prune
  `MISE_ALBUM_CHALLENGER_URL` block from `.env.example` (dead post-075).
- **Done:** both files accurate; nothing else reworded.
- **Verify:** `grep -i plutus\|mnemosyne CLAUDE.md` shows only the
  decommission note; `grep ALBUM_CHALLENGER .env.example` empty;
  unit suite still green (no code reads that env var — confirm with grep).
- **Risk:** normal.

### S3. Mobile-API helper consolidation (review §1)
- **What:** Extract the duplicated cursor codec + ETag/conditional helpers
  from `app/mobile_owner_api.py` and `app/mobile_gallery_calendar_api.py`
  into one `app/mobile_api_helpers.py`; point `mobile_client_api.py` at it.
  Behavior-preserving only — same wire cursors, same ETag formats.
- **Done:** one implementation of each helper; all three routers import it.
- **Verify:** `python -m pytest tests/test_mobile_*.py -q` green with zero
  test edits (the contract tests pin wire behavior).
- **Risk:** normal.

### S4. iOS `OwnerResource*` rename (IOS-UPGRADE step 7, after S1)
- **What:** Rename `OwnerResourceModel`/`OwnerResourceView`/`OwnerLoadState`
  → `ResourceModel`/`ResourceView`/`ResourceLoadState` (they're generic and
  used by both roles since M3). Pure mechanical rename, no behavior change.
- **Done:** rename complete, references updated, no `OwnerResource`
  identifiers remain.
- **Verify:** S1's CI job green (build + tests) — this is why S1 lands first.
- **Risk:** normal.

### S5. Client Home document-level deep links (IOS-UPGRADE step 7)
- **What:** `ClientHomeView` next-steps currently jump to the Documents
  *tab*; carry `documentVariant`+`documentID` through so the tap lands on
  the exact document detail view. Data is already in `NextStepAction`.
- **Done:** tapping a proposal/contract/invoice step opens its detail.
- **Verify:** new XCTest for the routing value + manual simulator check
  under S1's CI.
- **Risk:** normal.

### S6. M4a mutations — implementation (after O2 sets patterns; backend+iOS)
- **What:** Task check-off, booking cancel/reschedule per the existing
  `IOS-API-V1.md` commands: `Idempotency-Key` required,
  server-authoritative transitions, audit rows, rate limits; iOS side uses
  existing endpoint catalog entries.
- **Done:** endpoints live with contract tests (idempotent replay returns
  the same result; transition rules enforced); iOS screens wired.
- **Verify:** pytest contract tests incl. idempotency-replay and
  cross-principal denial; iOS tests under S1 CI.
- **Risk:** **red-light** (booking/money-adjacent state, audit trail) →
  reviewed draft PR, human merge.

## Suggested order

S2 → S1 → O1 → S3 → S4 → S5 → O2 → S6 → O4 → O3 (O3 whenever Kevin
answers). Nothing here is started tonight; this queue is the deliverable.
