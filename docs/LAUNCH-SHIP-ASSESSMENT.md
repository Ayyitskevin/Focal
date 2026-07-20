# Ship / do-not-ship assessment — Focal

**Date:** 2026-07-20  
**Branch:** `grok/launch-integrity` (PR #203 + RC acceptance follow-on)  
**Operator RC entry:** `scripts/rc-acceptance.py` · narrative: `docs/RC-ACCEPTANCE.md`

## Verdict

| Path | Status |
|---|---|
| **App Store / production tenants** | **DO NOT SHIP** until #179, #180, #185 replacement, and device gallery QA |
| **Sandbox PR merge** | **Merge-ready** after Kevin reviews red-light invoice lifecycle (#184) |

---

## What is now proven

| Area | Status | Evidence |
|---|---|---|
| Owner→client vertical (task, gallery, media, favorite, authz) | **Proven (RC suite)** | `tests/test_rc_acceptance.py` |
| Empty / draft / large-gallery bounds | **Proven** | same suite |
| Missing tenant storage fail-loud (no empty studio) | **Proven** | RC suite + `tests/test_tenant_storage_integrity.py` |
| Owner invoice preview ≠ client viewed | **Proven** | RC + `tests/test_invoice_owner_preview.py` |
| Owner draft media; video still/playback split | **Proven (API + structure)** | prior launch-integrity + RC structure checks |
| Large-gallery paging | **Proven** | #200 + RC + gallery API tests |
| Reviewer seeder fail-closed | **Proven** | tombstone + RC + seed tests |
| Operator-readable readiness | **Shipped** | `scripts/rc-acceptance.py` → pass/fail/blocked/n/a + always do-not-ship store line |

---

## Residual blockers

### Owner decisions (cannot invent)

1. **#179** — Privacy manifest / Connect labels  
2. **#180** — Storefront / IAP / free-companion CTAs  
3. **#185** — Safe App Review demo design  

Memo: `docs/APP-STORE-PRIVACY-AND-IAP-DECISIONS.md`

### Engineering residual

4. Device/simulator AVPlayer QA (Linux agent: **not applicable / blocked**)  
5. Close stale-open tracker items #186 / #199 after human confirm  
6. Human merge for money-adjacent invoice change  

---

## How to re-run evidence

```sh
source .venv/bin/activate
python -m pytest tests/test_rc_acceptance.py tests/test_rc_acceptance_readiness.py -q
python scripts/rc-acceptance.py
python -m pytest tests/ -m unit
ruff check . && ruff format --check .
```
