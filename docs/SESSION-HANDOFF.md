# SESSION HANDOFF — full-app review + iOS upgrade plan + execution queue

**Mission:** review Mise end-to-end (docs/MISE-REVIEW.md), write the iOS
upgrade plan (docs/IOS-UPGRADE.md), and produce an Opus/Sonnet execution
queue (docs/HANDOFF-QUEUE.md). Review and plan only — no app code edits.
Kevin is mid-reskin on his machine; treat uncommitted changes as live work.

## Checklist

- [ ] AI-capability topology confirmed from adapter code (checkpoint 1)
- [ ] Deliverable 1: docs/MISE-REVIEW.md (checkpoint 2)
- [ ] iOS app audit sub-step of Deliverable 2 (checkpoint 3)
- [ ] Deliverable 2: docs/IOS-UPGRADE.md (checkpoint 4)
- [ ] Deliverable 3: docs/HANDOFF-QUEUE.md (checkpoint 5)

## Key findings so far

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

Read `app/providers/contracts.py`, `app/providers/registry.py`, and every
adapter in `app/providers/` to settle the AI-capability topology
(in-process vs hosted API vs separate self-hosted service, per capability:
Argus, Plutus, Mnemosyne, Aphrodite, Dionysus). Record the finding here
and commit before writing any deliverable.

## Git state

- Branch: `claude/mise-review-ios-plan` (from origin/main @ b641388)
- Pushed: not yet
- Draft PR: none yet (open when first real deliverable content lands)

## Open questions for Kevin

- (none yet)

## Resume message

Read docs/SESSION-HANDOFF.md on claude/mise-review-ios-plan and continue
from the next concrete step.
