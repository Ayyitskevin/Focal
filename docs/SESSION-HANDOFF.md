# SESSION HANDOFF — full-app review + iOS upgrade plan + execution queue

**Mission:** review Mise end-to-end (docs/MISE-REVIEW.md), write the iOS
upgrade plan (docs/IOS-UPGRADE.md), and produce an Opus/Sonnet execution
queue (docs/HANDOFF-QUEUE.md). Review and plan only — no app code edits.
Kevin is mid-reskin on his machine; treat uncommitted changes as live work.

## Checklist

- [x] AI-capability topology confirmed from adapter code (checkpoint 1)
- [ ] Deliverable 1: docs/MISE-REVIEW.md (checkpoint 2)
- [ ] iOS app audit sub-step of Deliverable 2 (checkpoint 3)
- [ ] Deliverable 2: docs/IOS-UPGRADE.md (checkpoint 4)
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

Run the health checks (`python -m pytest -m unit -q`, `ruff check .`,
`ruff format --check .` — report only), skim `docs/WORKER-CONTRACT.md` +
`docs/adr/README.md` for cross-references, check templates/ + static/ for
reskin consistency (admin/* vs client/*), then write docs/MISE-REVIEW.md.
A working venv exists at
/tmp/claude-0/-home-user-mise/0214d61e-6505-5f50-a638-1150d7caf19b/scratchpad/venv312
(python3.12 + requirements + pytest + httpx2 + ruff; export
MISE_SECRET_KEY=anything before pytest).

## Git state

- Branch: `claude/mise-review-ios-plan` (from origin/main @ b641388)
- Pushed: yes
- Draft PR: none yet (open when first real deliverable content lands)

## Open questions for Kevin

- (none yet)

## Resume message

Read docs/SESSION-HANDOFF.md on claude/mise-review-ios-plan and continue
from the next concrete step.
