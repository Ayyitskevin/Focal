# Ship / do-not-ship assessment — Focal

**Date:** 2026-07-20 (post-merge #203 reconciliation)  
**Tip:** `main` @ `2a3e1dca5a871d79507c256b4cbdce8ff8bfbd64`  
**RC entry:** `scripts/rc-acceptance.py` · matrix: `docs/LAUNCH-INTEGRITY-MATRIX.md` · `docs/RC-ACCEPTANCE.md`

## Verdict

| Path | Status |
|---|---|
| **App Store / production tenants** | **DO NOT SHIP** until #179, #180, #185 replacement, and device gallery QA |
| **Sandbox `main` engineering integrity** | **Ready** for continued green-light development; RC readiness READY on eng gates with STORE SHIP do-not-ship |

## Proven on main (after #203)

| Area | Status |
|---|---|
| Owner→client RC vertical | Proven — `tests/test_rc_acceptance.py` |
| Invoice owner preview ≠ client viewed (#184 eng) | Proven |
| Owner draft media + video presentation structure (#183 partial) | Proven API/structure; device QA residual |
| Gallery paging (#199 eng) | Proven on main since #200 |
| Storage fail-loud (#181) | Proven |
| Seeder fail-closed (#185 containment) | Proven |
| RC readiness fail-honest timeout/tool | Proven — timeout/missing interpreter → **fail** not READY |
| CI on merge #203 | Green — CI + iOS workflows on main |

## Residual blockers (do not invent)

1. **#179** privacy labels / C617.1 / Product Interaction — owner decision  
2. **#180** storefront/IAP/free-companion — owner decision  
3. **#185** safe App Review demo replacement — design hold (containment only)  
4. **Device AVPlayer QA** for #183 video playback  
5. Tracker hygiene: #184/#199/#186 may be closable after human confirm — **not auto-closed here**

## Re-run

```bash
python scripts/rc-acceptance.py
# Expect: READY eng gates + STORE SHIP: do-not-ship
```
