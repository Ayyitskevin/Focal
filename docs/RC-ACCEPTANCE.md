# Release-candidate acceptance — Focal

**Date:** 2026-07-20 (post-merge reconciliation)  
**Tip:** `origin/main` after PR #203 (`2a3e1dc…`)  
**Operator entry:** `.venv/bin/python scripts/rc-acceptance.py`  
**App Store auditor:** `.venv/bin/python scripts/app-store-readiness.py` (read-only; eng READY ≠ Connect ship)  
**Issue matrix:** `docs/LAUNCH-INTEGRITY-MATRIX.md`

## Verdict

| Surface | Recommendation |
|---|---|
| Sandbox PR merge | **Merge-ready** after human review of prior red-light invoice lifecycle change (#184) |
| App Store / TestFlight | **Do not ship** until #179, #180, and #185 replacement are decided/implemented |
| Hosted production tenants / flow deploy | **Do not touch** from this sandbox |

---

## What RC acceptance now proves

Automated suite: `tests/test_rc_acceptance.py` (unit-marked, real FastAPI entry points).

| Scenario | Proven behavior | Evidence |
|---|---|---|
| Owner → client vertical | Owner login → task completion → gallery list/detail → client unlock of **only** authorized gallery → media GET → favorite → owner sees favorite_count +1; rival gallery 404/403; invoice stays `sent` | `test_rc_owner_client_vertical_task_gallery_favorite_media` |
| Empty gallery | Owner detail 200 with empty assets; studio still has other rows (not false empty-studio) | `test_rc_empty_gallery_is_honest_empty_not_false_studio` |
| Draft gallery | Owner media 200; guest unlock fail-closed; anonymous media 401 | `test_rc_draft_owner_media_guest_denied` |
| Large gallery | First page ≤100, `assets_has_more`, next cursor reaches remainder | `test_rc_large_gallery_first_page_bounded` |
| Invoice preview | Admin session on `/i/{slug}` does not mint viewed; public client flips once | `test_rc_owner_invoice_preview_does_not_mint_client_view` |
| Missing tenant storage | Hosted middleware 503 + `request_id`, no recreation of empty studio, operator alert | `test_rc_missing_tenant_storage_fail_loud_no_empty_recreation` |
| Reviewer seeder | `seed_demo_tenant` raises SystemExit before config/DB | `test_rc_seed_demo_tenant_fail_closed` |
| Native capability structure | AR does not Link publicURL; video uses presentation helper + AVPlayer path | structural tests in same file |

Integrity regressions remain green: `tests/test_tenant_storage_integrity.py`, `tests/test_seed_demo_tenant.py`, `tests/test_mobile_gallery_calendar_api.py`.

### Operator command

```sh
source .venv/bin/activate
python scripts/rc-acceptance.py           # human-readable pass/fail/blocked/n/a
python scripts/rc-acceptance.py --json    # machine-readable
python scripts/rc-acceptance.py --no-tests  # structural only (fast)
```

Exit code **0** = no **fail** lines (product gates green). **Blocked** / **n/a** (e.g. Xcode on Linux, hosted preflight when not SAAS_MODE) do not fail the exit code.  
**STORE SHIP** is always printed as **do-not-ship** with #179/#180/#185 reason — this tool never claims App Store readiness.

---

## Capability truth (acceptance path only)

| Principal / action | Expected | Checked |
|---|---|---|
| `studio_owner` + `studio:write` | Task completion succeeds | pass (vertical) |
| `studio_owner` gallery list | Includes draft + published | pass |
| `studio_owner` draft media | 200 despite unpublished | pass |
| `gallery_guest` own gallery media/favorite | 200 / selected | pass |
| `gallery_guest` rival gallery detail | 404 | pass |
| `gallery_guest` rival favorite | 403 | pass |
| Guest unlock unpublished draft | 401/403/404 | pass |
| Portal/workspace favorites | Not in this vertical | **n/a** (covered elsewhere) |
| StoreKit / IAP | Not implemented | **n/a** — owner decision #180 |
| Reviewer demo as production capability | Fail-closed tombstone | pass (not a production path) |

Docs must not claim native gallery “complete on device” without Xcode QA (status **blocked/n/a** on Linux).

---

## Remaining launch blockers

1. **#179** — App Store privacy manifest/labels (owner decision; memo in `docs/APP-STORE-PRIVACY-AND-IAP-DECISIONS.md`)
2. **#180** — Storefront / IAP / free-companion (owner decision; same memo)
3. **#185** — Safe App Review demo tenant (engineering design hold; seeder contained only)
4. **Device AVPlayer QA** — environment limit on Linux CI/agent
5. **Tracker hygiene** — #186 / #199 implemented on main but still open in GitHub

---

## Unresolved owner decisions (do not invent)

See numbered checklist in `docs/APP-STORE-PRIVACY-AND-IAP-DECISIONS.md` (Product Interaction, C617.1, storefront territory, free-companion CTAs, IAP path, reviewer credentials).

---

## Relation to prior launch-integrity work

This milestone **composes** launch-integrity fixes (#184 invoice isolation, #183 owner draft media + video still/playback, storage fail-loud, seeder tombstone, paging) into one operator-runnable acceptance entry. It does not re-open money-path policy or App Store submission.
