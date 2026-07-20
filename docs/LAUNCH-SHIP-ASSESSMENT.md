# Ship / do-not-ship assessment — Focal launch integrity

**Date:** 2026-07-20  
**Branch:** `grok/launch-integrity` (from `origin/main` `55e1787`)  
**Verdict: DO NOT SHIP to App Store / production tenants until owner decisions and residual holds below are closed.**

Ordinary development, peer review, and merge of this PR (after Kevin’s red-light review of the invoice lifecycle change) remain appropriate.

---

## What this milestone makes true

| Area | Status | Evidence |
|---|---|---|
| Owner invoice preview no longer mints client “viewed” | **Fixed (this PR)** | `app/public/pay.py` admin-session skip + one-time update; native AR in-app preview without `publicURL` open; `tests/test_invoice_owner_preview.py` |
| Client public invoice first-view still works once | **Fixed / preserved** | Same tests + existing smoke lifecycle |
| Owner can load draft gallery media | **Fixed (this PR)** | `app/mobile_media.py`; `tests/test_mobile_media_owner_draft.py` |
| Video still vs playback split | **Fixed (this PR)** | Poster still + authenticated `VideoPlayer`; structure tests; Swift unit tests (macOS/Xcode to execute) |
| Large-gallery paging | **Already on main** | PR #200; gallery API tests green |
| Tenant storage fail-loud | **Already on main** | PR #198; `tests/test_tenant_storage_integrity.py` green |
| Reviewer seeder fail-closed | **Already on main** | PR #188/#195; tombstone + tests green |
| Capability docs match authority | **Already on main** | PR #193; access-doc tests green |

---

## Residual blockers (do not ship past these)

### Owner decisions (cannot invent)

1. **#179 — Privacy manifest / Connect labels** — Product Interaction disclosure; remove or justify `C617.1`; Focal naming in Connect pack. Memo: `docs/APP-STORE-PRIVACY-AND-IAP-DECISIONS.md`.
2. **#180 — Storefront / IAP / free-companion** — web purchase CTAs vs guidelines; ADR 0070 still Proposed. Same memo.
3. **#185 — Safe App Review demo tenant** — containment only; replacement design + hosted credentials still required for Review.

### Engineering residual (non-blocking for sandbox merge; blocking for store claims)

4. **Device QA for AVPlayer** — Linux agent host cannot run Xcode; video playback needs a device/simulator pass before claiming native gallery “complete.”
5. **Tracker hygiene** — #186 and #199 appear implemented on main but still OPEN; close after human confirm (matrix marks stale-open).
6. **Invoice lifecycle is red-light** — this PR must be human-merged (AGENTS.md money path).

---

## Environment limits (honest)

| Gate | Result on agent host |
|---|---|
| `pytest` unit + focused integrity | Run; see scratch logs |
| `ruff check` / `ruff format --check` | Run on branch |
| Full non-unit smoke suite | Run with throwaway `MISE_DATA_DIR` when env allows |
| iOS `xcodebuild test` | **Not available** on this Linux node — Swift tests committed; structure tests cover source contracts |
| ffmpeg / media derivatives | Not required for unit media path tests (bytes written directly) |
| App Store Connect / TestFlight | **Out of scope — not performed** |

---

## Recommendation

- **Merge path:** Open PR → Kevin reviews money-adjacent invoice change → merge when gates green.  
- **App Store path:** **Do not ship** until #179, #180, and #185 replacement are decided/implemented and device gallery QA is green.  
- **Hosted production tenants / flow deploy:** **Do not touch** from this sandbox PR.
