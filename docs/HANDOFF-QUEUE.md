# Execution queue — from MISE-REVIEW.md + IOS-UPGRADE.md (2026-07-12)

Ordered task list for cold pickup. Every task: done criteria, real
verification, risk tag. **Red-light** = money path, auth/CSRF, migrations,
contracts, or deploy per CLAUDE.md → reviewed draft PR only, never
self-applied. Sources: `docs/MISE-REVIEW.md` (review), `docs/IOS-UPGRADE.md`
(plan), `docs/SESSION-HANDOFF.md` (topology detail).

> **Current status (2026-07-17):** S6a/S6b, native task/cancel wiring, and the
> S6c/S6e booking-reschedule backend are merged. The source-aware slot feed is
> restored by the current mainline recovery. Native reschedule was orphaned when
> PR #164 merged into an already-merged feature branch; it remains a separate
> red-light recovery pending human review and deliberate capability activation.
> The decomposition below is preserved as the execution record from the review.

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

### O2. Commercial-spine mobile API design (IOS-UPGRADE step 3) — DONE
- **What:** Design the read-only `/api/v1` surface for AR chase assist,
  closeout-readiness, company next-action ranking, and the Studio Activity
  commercial queue.
- **Delivered:** the "Owner commercial spine (planned — read-only)" section
  in `docs/IOS-API-V1.md` — 5 owner-only read endpoints, DTOs mirroring the
  exact admin derivations (`_ctx_commercial_actions`, `_company_next_actions`,
  `_ar_chase_context`/`_company_overdue_rows`/`_ar_chase_history`,
  `_project_closeout` in `app/admin/studio.py`), the `href → structured
  target` translation, cents→`Money`, and the "company = root client
  (`parent_id IS NULL`)" model. AR-chase *send* explicitly deferred to M4.
  Implementation split into S7/S8/S9 below.
- **Verified:** every field traced to a real admin dict key + `file:line`;
  no new authority invented; the one mutation (AR-chase send) is excluded.
- **Risk:** normal (design doc, merged).

### S7. Extract commercial-spine query functions (prerequisite for S8)
- **What:** The six computations O2 depends on are `_`-prefixed privates in
  `app/admin/studio.py` (`_ctx_commercial_actions`, `_company_next_actions`,
  `_ar_chase_context`, `_company_overdue_rows`, `_ar_chase_history`,
  `_project_closeout`). Lift them into an importable module (e.g.
  `app/commercial.py`) with no behavior change, so both the HTML admin routes
  and the new DTO router call one implementation (IOS-API-V1 backend note 6).
- **Done:** functions importable from the new module; `app/admin/studio.py`
  imports them; admin pages render identically.
- **Verify:** full unit + smoke suite green with zero admin-template test
  edits (the studio/company/activity page tests pin the rendered output).
- **Risk:** normal (refactor of live admin code — behavior-preserving; reviewed PR).

### S8. Implement the commercial-spine DTO router (backend)
- **What:** New `app/mobile_commercial_api.py` (owner-only, `studio:read`),
  mounting the 5 endpoints from the IOS-API-V1 section, reusing the S7
  functions, with the `OwnerAPIModel`/`Money` DTO conventions and the
  `href → target` mapping owned server-side. Read-only; no AR-chase send.
- **Done:** endpoints live in the scoped OpenAPI; DTOs match the doc.
- **Verify:** contract tests — owner sees data, every non-owner principal gets
  403, unknown/non-root company id 404, no admin `href` string ever
  serialized, money as integer `minor_units`, cursor pages re-authorize.
- **Risk:** **red-light** (reads AR/invoice data + adds contract surface) →
  reviewed draft PR, human merge.

### S9. iOS commercial-spine screens
- **What:** `CommercialRepository` + owner screens for the action queue,
  company next-actions, AR-chase assist (read/preview), and project closeout,
  wired to the S8 endpoints and the typed `target` router. Web fallback for
  the AR-chase send until its M4 command exists.
- **Done:** screens render against the endpoints; targets navigate correctly.
- **Verify:** XCTest fixtures for DTO decode + target routing; green under the
  iOS CI gate (S1).
- **Risk:** normal (read-only client of an existing contract).

### O3. iOS distribution decision (IOS-UPGRADE step 6 — blocked on Kevin)
- **What:** Resolve the open product question: single-tenant
  kleephotography app vs hosted multi-tenant awareness. Then scope
  branding, `MiseServerBaseURL` provisioning, TestFlight/App Store path.
- **Done:** decision recorded (ADR or IOS-ARCHITECTURE §11 update) with
  Kevin's explicit answer; queue items appended for the chosen path.
- **Verify:** decision doc names the distribution channel and its review
  requirements; no code assumption collapsed before the decision.
- **Risk:** normal (product decision); blocks nothing else in this queue.

### O4. Sidecar credential hygiene review (security flag from review §6) — DONE
- **What:** Investigate the three flags: static long-lived sidecar bearer
  tokens with no rotation story; plain-`http://` challenger endpoints
  carrying client-media derivatives; post-075 stale homelab config in
  `.env.example`. Decide rotation policy and transport requirements —
  partially subsumed by O1 (consolidation removes most sidecar auth).
- **Delivered (ADR 0069, draft PR):** disposition per flag — flag 3
  **remediated** (S2 removed `MISE_ALBUM_CHALLENGER_URL`; grep clean); flag 1
  **scheduled** — interim rotation policy + `docs/SECURITY.md` procedure over
  the ADR 0065 cadence (inbound gates already constant-time + fail-disarmed),
  mechanism deferred to O1; flag 2 **partially remediated** — a non-auth
  startup cleartext-transport WARNING (`config.insecure_sidecar_endpoints()` +
  `app/main.py` lifespan, unit-tested), hard scheme-enforcement deferred as a
  sign-off-gated red-light PR (can't hard-fail the live Argus default). Rule:
  https on non-loopback; http only to loopback.
- **Verify:** each flag remediated / scheduled / accepted with rationale —
  see ADR 0069 disposition table; unit suite + ruff green; no live path,
  auth check, or transport altered by the shipped code.
- **Risk:** **red-light** (auth) → reviewed draft PR only. The two auth-touching
  follow-ups (hard scheme enforcement; rotation mechanism) are named and left
  for their own reviewed PRs.

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
  `IOS-API-V1.md` commands: task completion and cancellation are naturally
  idempotent without a key, while reschedule requires `Idempotency-Key`;
  all keep server-authoritative transitions, audit rows, and rate limits.
  The iOS side uses existing endpoint catalog entries.
- **Done:** endpoints live with contract tests (naturally idempotent repeats
  or keyed replay return the stable result; transition rules enforced); iOS
  screens wired.
- **Verify:** pytest contract tests including natural idempotency, keyed
  replay, and cross-principal denial; iOS tests under S1 CI.
- **Risk:** **red-light** (booking/money-adjacent state, audit trail) →
  reviewed draft PR, human merge.
- **Decomposition (mapped from the real server flows):** one command per PR,
  risk ascending. S6c adds the first `Idempotency-Key` replay store; the M3
  favorite toggle and S6a/S6b got idempotency from their natural PUT/DELETE or
  guarded-transition semantics.
  - **S6a — owner task check-off — DONE (merged):** first owner *write*.
    `PUT`/`DELETE /api/v1/tasks/{id}/completion` (naturally idempotent, no key),
    requires `studio:write`, one `audit_log` row per real transition
    (`task`/`complete`|`reopen`, actor `owner`). Backend + 7 contract tests
    (idempotent replay, reopen, 404, guest-refused, read-only-owner-refused).
    Mirrors `admin/activity.py::task_toggle` semantics; the web path writes no
    audit row, the native one does. iOS wiring deferred to S6d.
  - **S6b — booking cancel — DONE (merged):** owner-only
    `POST /api/v1/bookings/{id}/cancel`, naturally idempotent via the guarded
    confirmed→cancelled transition; audited and fires `booking_notify.cancelled`.
  - **S6c — booking reschedule + Idempotency-Key store — IMPLEMENTED (backend;
    stacked under S6e):** owner-only atomic create-new + cancel-old with
    server-side slot/policy revalidation, linkage/intake carryover, two audit
    rows, a session-bound hashed UUID replay receipt, expiry cleanup, and
    exact-response replay. Migration 082 keeps this red-light.
  - **S6d — iOS task/cancel wiring — IMPLEMENTED (draft PR #161):** native task
    completion/reopen and booking cancellation are wired. Native reschedule stays
    hidden until the S6e backend draft is reviewed and activated.
  - **S6e — durable booking workflow dispatch — IMPLEMENTED (backend; stacked
    red-light draft):** migration 083 inserts six tenant-local effect rows in the
    same transaction as S6c. The old client UID receives CANCEL before the
    replacement UID can receive REQUEST; studio mail, Notion, and Google Calendar
    effects have independent leased retry state. Completed effects are never reset,
    stale leases recover on boot/short sweeps, and owner-only status/manual retry
    routes expose bounded state; retry revalidates the owner session and audits the
    reset under one writer lock. A lifecycle transition may retire an expired lease
    even while delivery is disarmed, so cancellation cannot deadlock after a worker
    crash. Migration 083 preserves every previously issued
    calendar UID and gives new bookings tenant-scoped identity. Cancelling or
    chaining a replacement atomically supersedes its stale queued effects across
    mobile, public, and admin entry points; provider work with an active lease
    returns a retryable conflict. Hosted recovery opens retained tenant DBs existing-only,
    so a delete race cannot recreate storage. Auth advertises
    `booking.reschedule` only while the default-off workflow flag and mailer are
    armed. Delivery is intentionally at-least-once (stable Message-ID, with a
    documented crash-after-acceptance duplicate window), not falsely exactly-once.

## Suggested order

Original: S2 → S1 → O1 → S3 → S4 → S5 → O2 → S6 → O4 → O3.

**Done (merged):** S2, S1 (incl. fixing the compile errors it surfaced — the
iOS app had never been built), O1 (ADR 0068), S3, S7, S8, S9, S4, S5, O4
(ADR 0069), S6a, S6b. **Done (design):** O2 — which spawned S7 → S8 → S9.
**Implemented (red-light drafts):** S6c + S6e backend; S6d task/cancel iOS.

**Remaining:** human review/merge of S6c+S6e → S6d reschedule wiring → O3.
Reschedule stays unexposed in iOS until the stacked backend drafts are reviewed
and the server capability is deliberately armed. S6e and the AR-chase send
command are red-light and wait on reviewed PRs. O4's two
auth-touching follow-ups (hard scheme enforcement; a rotation mechanism) are
likewise red-light and named in ADR
0069. O3 (iOS distribution) and the Dionysus fate (from O1) are the two
decisions still open for Kevin.
