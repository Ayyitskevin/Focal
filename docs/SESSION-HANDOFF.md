# SESSION HANDOFF — full-app review + iOS upgrade plan + execution queue

**Mission:** review Mise end-to-end (docs/MISE-REVIEW.md), write the iOS
upgrade plan (docs/IOS-UPGRADE.md), and produce an Opus/Sonnet execution
queue (docs/HANDOFF-QUEUE.md). Review and plan only — no app code edits.
Kevin is mid-reskin on his machine; treat uncommitted changes as live work.

## Checklist

- [x] AI-capability topology confirmed from adapter code (checkpoint 1)
- [x] Deliverable 1: docs/MISE-REVIEW.md (checkpoint 2)
- [x] iOS app audit sub-step of Deliverable 2 (checkpoint 3)
- [x] Deliverable 2: docs/IOS-UPGRADE.md (checkpoint 4)
- [ ] Deliverable 3: docs/HANDOFF-QUEUE.md (checkpoint 5)

## Key findings so far

### AI-capability topology (checkpoint 1 — CONFIRMED from adapter code)

Per capability, against the mission's (a)/(b)/(c) taxonomy:

| Capability | Adapter | Topology | Calls |
|---|---|---|---|
| VISION (Argus, production) | `LegacyArgusVisionAdapter` → `app/argus_analyze.py` | **(c) separate self-hosted service** | HTTP + bearer to `MISE_ARGUS_URL` (Argus sibling service; Argus itself uses cloud Grok per runbook) |
| VISION challenger (eval-only) | `InternalVisionChallengerAdapter` (`vision_challenger.py:75`) | **(c) — Kevin's homelab explicitly** | OpenAI-compatible POST to `MISE_VISION_CHALLENGER_URL`, documented as `http://mickeybot:11434/v1` (Ollama Qwen3-VL); dormant until env set; `serves_production=False` + registry interlock (`registry.py:150`) |
| CONTENT caption (Odysseus) | `LegacyOdysseusCaptionAdapter` → `app/caption_ai.py:45` | **(c) separate self-hosted service** | `urlopen` to `MISE_ODYSSEUS_CAPTION_URL`; Odysseus owns model routing |
| CONTENT packs (Dionysus) | `LegacyDionysusPackAdapter` → `app/platekit.py` | **(c) separate self-hosted service** | HTTP to `MISE_PLATEKIT_API_BASE` (alias `MISE_DIONYSUS_API_BASE`) |
| PRODUCTS (Aphrodite) | `ProductsRenderAdapter` | dormant — **no outbound call ever** (`render` returns non-OK without calling; `serves_production=False`) | would-be `MISE_PRODUCTS_RENDER_URL` |
| OFFERS (Plutus), ALBUMS (Mnemosyne) | — | **decommissioned** (migration `075_decommission_albums_offers.sql`); CLAUDE.md's five-capability list is stale | — |

Nothing is (a) in-process and nothing calls a hosted model API directly from
Mise: **every live capability depends on a separate self-hosted sidecar**,
and the vision/album challenger URLs are documented homelab endpoints
(`mickeybot`). No sidecar is co-deployed in `docker-compose.yml`. This is
exactly the case the architecture directive rules unacceptable for hosted
data → the consolidation plan is an Opus queue item (Deliverable 3), NOT
tonight's work. `docs/MISE-CONSOLIDATION-ROADMAP.md` already sketches the
strangler path; `registry.py` is the seam. §11.4: every adapter returns
`ReviewRequirement.HUMAN_REVIEW` and adapters never write the DB.
`.env.example:138` also references a dormant album challenger URL on
mickeybot — stale post-075, worth pruning.

- This checkout's working tree is **clean** — the reskin's uncommitted
  changes exist only on Kevin's machine, not here. Review is against
  origin/main @ b641388 (includes iOS Milestone 3, PR #144, merged today).
- Established in the prior session on this container (do not re-derive):
  the iOS app is SwiftUI/iOS 17/Swift 6 (XcodeGen), authenticates via the
  opaque-bearer `/api/v1` boundary (ADR 0066/0067), Milestones 0–3 are
  implemented and merged; it talks **only** to the FastAPI backend, never
  to any AI capability directly. Backend unit suite: 693 tests green;
  `test_smoke.py` failures in this container are environment-only
  (missing ffmpeg). ruff check + format clean at merge time.

## Next concrete step

Deliverable 3 (docs/HANDOFF-QUEUE.md): turn MISE-REVIEW.md's lane ratings +
IOS-UPGRADE.md's 7 steps into an ordered queue split Opus (judgment-heavy:
AI-sidecar consolidation plan, commercial-spine API design, distribution
decision) / Sonnet (mechanical: iOS CI job, CLAUDE.md doc fixes,
.env.example pruning, cursor/ETag helper consolidation, rename). Every task
needs done criteria + real verification + risk tag; money/auth/migrations/
contracts/deploy = red-light → reviewed draft PR. Then final handoff update
+ PR body update. Draft PR is #145.

### iOS audit facts (established; verified in the M3 session today)

- SwiftUI, iOS 17 minimum, Swift 6 strict concurrency, XcodeGen
  (`ios/project.yml`), Observation-based MVVM, zero third-party deps.
- Auth: opaque rotating bearer sessions against `/api/v1` (ADR 0066);
  ThisDeviceOnly Keychain, origin-scoped; Face ID/Touch ID is a local
  re-entry lock only. No cookies, redirects rejected.
- API surface called: tenant/auth/client-auth/me/sessions, dashboard,
  clients, projects, galleries(+detail), event-types, bookings,
  client/home, client/galleries(+detail), client/bookings,
  projects/{id}/proposals|contracts|invoices, favorite PUT/DELETE,
  /api/v1/media/* via AuthenticatedMediaLoader.
- The app talks ONLY to the Mise backend — no AI capability directly.
- Milestones 0–3 implemented/merged; M4 (safe mutations) and M5 (APNs,
  deep links, ops) are the planned next slices per IOS-ARCHITECTURE.md.
- Gaps: no commercial-spine surfaces (review §5); Swift tests exist but
  no CI job runs them (no macOS runner configured in .github/workflows);
  fonts decision pending (system serif stands in for Newsreader).
- Open product question for Kevin: single-tenant kleephotography use only,
  or eventual hosted multi-tenant awareness? (Do not collapse.)

## Git state

- Branch: `claude/mise-review-ios-plan` (from origin/main @ b641388)
- Pushed: yes
- Draft PR: #145 (docs-only; keep pushing to it; never self-merge)

## Open questions for Kevin

- Does the iOS app serve kleephotography single-tenant use only, or does
  it need eventual hosted multi-tenant awareness? (IOS-UPGRADE.md step 6
  is blocked on this; nothing else is.)
- Sidecar consolidation: confirm the target is "inside the app's
  deployable unit, hosted model APIs as vendors OK" — assumed from the
  architecture directive; the Opus queue item is scoped that way.

## Resume message

Read docs/SESSION-HANDOFF.md on claude/mise-review-ios-plan and continue
from the next concrete step.
