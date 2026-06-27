# Sibling briefs — the prompt playbook for catching the workers up to the OS

Ready-to-paste prompt sequences for bringing each sibling repo up to the Mise Solo Studio OS
vision — the **Exodia** assembly: every engine becomes a stateless, contract-true worker Mise
can drive, compare, meter, and retire. Each brief is the executable form of
[`../SIBLING-IMPROVEMENT-PLAN.md`](../SIBLING-IMPROVEMENT-PLAN.md), conforming to
[`../WORKER-CONTRACT.md`](../WORKER-CONTRACT.md) and the [`../../schemas/`](../../schemas/).

## How to use

Each brief has two modes — pick one per repo:

- **Mode A — umbrella.** Paste the single "review & align" prompt; the agent audits the repo
  and self-sequences its own PRs. Fewer pastes, less control.
- **Mode B — discrete sequence.** Paste prompts `#1, #2, …` one at a time, merging each PR
  before the next. More control, releasable at every step. **Recommended for the blitz.**

Every prompt is **self-contained** — it inlines the contract bits it needs, because the
sibling session can't see this repo. Copy one block at a time.

## Priority order (do them in this order)

| # | Repo | Brief | Why | When |
| --- | --- | --- | --- | --- |
| 1 | **Argus** (vision) | [ARGUS.md](ARGUS.md) | Unblocks the vision cutover | now |
| 2 | **Plutus** (offers) | [PLUTUS.md](PLUTUS.md) | SKU linkage makes the revenue scorecard real | now |
| 3 | **Dionysus** (content) | [DIONYSUS.md](DIONYSUS.md) | One content provider; conform-or-retire | now |
| 4 | **Mnemosyne** (albums) | [MNEMOSYNE.md](MNEMOSYNE.md) | Beat the baseline album proposer | when albums go live |
| 5 | **Aphrodite** (products) | [APHRODITE.md](APHRODITE.md) | Render worker w/ real spend | after the products business calls |

## Shared guardrails (true for every prompt)

- **Audit first.** The agent delivers a gap analysis + plan and **waits for go** before
  sweeping. Don't remove that line — it's what stops an aggressive model rewriting the repo.
- **Develop on `claude/...` branches; every change a draft PR a human merges.**
- **Backward-compatible defaults**; each PR independently green (lint + tests).
- **No secrets in code; mock-only / reproducible CI — no live model/API calls in tests.**
- **Keep the existing production path working** until a flagged, measured cutover proves the
  replacement; rollback is always a flag.

## Do NOT build briefs for these

- **Odysseus** — third-party local AI UI (an inference endpoint you self-host); nothing to
  improve, Mise just points at it.
- **Hestia** — doctrine only; no runtime worker.
- **Athena / Midas** — do not integrate (second source of truth / out of scope).
