# Launch integrity matrix — Focal (post-merge reconciliation)

**Reconciled against:** `origin/main` `2a3e1dca5a871d79507c256b4cbdce8ff8bfbd64`  
**Merge:** PR #203 (`Launch integrity + RC acceptance`) · CI green on main (Python + iOS)  
**Date:** 2026-07-20  
**Operator RC entry:** `python scripts/rc-acceptance.py` · narrative `docs/RC-ACCEPTANCE.md`

This matrix is the post-merge audit of issues named in the release-integrity goal.
It does **not** close GitHub issues by fiat. Rows marked *stale-open* need human
confirm before close. **STORE SHIP remains do-not-ship** until owner decisions
and residual holds below clear.

Disposition legend:

| Disposition | Meaning |
|---|---|
| **fully-satisfied (eng)** | Acceptance criteria met by code+tests on `main`; tracker may still be open |
| **partial (eng)** | Core eng paths proven; residual device QA or incomplete parent AC |
| **containment-only** | Unsafe path fail-closed; full product replacement still held |
| **owner decision** | Product/legal/policy choice required; no invented policy |
| **stale-open tracker** | Shipped on main earlier; issue still OPEN — recommend close after human review |

| Issue | Title | Disposition | Evidence on `main` @ `2a3e1dc` | Do not close until… |
|---|---|---|---|---|
| **#179** | App Store privacy manifest/label inaccurate | **owner decision** | `PrivacyInfo.xcprivacy` still: email/name/userID/deviceID only; **C617.1** still declared; no `ProductInteraction`. Memo: `docs/APP-STORE-PRIVACY-AND-IAP-DECISIONS.md`. | Kevin decides labels + C617.1; Connect worksheet updated |
| **#180** | Storefront/IAP strategy | **owner decision** | Web CTAs remain (`Start a studio` → `/pricing`, Manage billing). No StoreKit in tree. ADR 0070 Proposed. Same memo. | Kevin picks storefront/IAP/free-companion; no silent CTA policy invent |
| **#183** | Native gallery drafts / video / large | **partial (eng)** | Owner draft media: `mobile_media._delivery_eligible_for_principal` + `tests/test_mobile_media_owner_draft.py` + RC suite. Video poster/playback: `GalleryMediaPresentation` + `AuthenticatedRemoteVideo`. Paging: #200 + gallery API tests + RC large-gallery. | Device/simulator AVPlayer QA; human confirm before closing parent |
| **#184** | Owner invoice preview → client viewed | **fully-satisfied (eng)** | `_record_client_first_view` + admin skip; native AR in-app Preview (no `publicURL` Link); `tests/test_invoice_owner_preview.py` + RC vertical. | Human confirm + optional tracker close (money path already on main via #203) |
| **#185** | Reviewer-demo seeder unsafe for hosted | **containment-only** | `scripts/seed_demo_tenant.py` tombstone (no app/DB import); fail-closed tests + RC. Full safe replacement **not** built. | Human-approved replacement design + hosted credentials |
| **#199** | Paginate native gallery assets | **stale-open tracker** | PR #200 on main; `assets_has_more` / cursor; tests green under RC integrity suite. | Human confirm close (child of #183; does not alone close #183) |

### Adjacent (not in named set; still relevant)

| Item | Disposition | Notes |
|---|---|---|
| #181 tenant storage fail-loud | **fully-satisfied (eng)** | #198 on main; `tests/test_tenant_storage_integrity.py` + RC storage probe |
| #186 client navigation | **stale-open tracker** | #191 on main |
| T3 App Review credentials | **held** | Blocked on #185 replacement |
| ADR 0070 | **Proposed** | Tied to #180 |

---

## Fail-honest probes re-run on this tip

| Probe | Expected | Observed (post-merge main) |
|---|---|---|
| Required suite timeout | readiness **fail**, not READY | `run_pytest_suite` → `fail` (timeout) |
| Missing Python interpreter | readiness **fail** | `FileNotFoundError` → `fail` |
| Missing tenant storage | 503 + no empty studio | RC + tenant storage suite green |
| Reviewer seeder | SystemExit before config/DB | tombstone + tests green |
| Owner invoice preview | status stays `sent` | invoice + RC tests green |
| Owner draft / guest media | owner 200 / guest denied | media owner draft + RC tests green |
| Large gallery first page | ≤100 + has_more | RC + gallery calendar tests green |
| STORE SHIP line | always **do-not-ship** | `scripts/rc-acceptance.py` prints do-not-ship for #179/#180/#185 |

Commands:

```bash
python scripts/rc-acceptance.py
python -m pytest tests/test_rc_acceptance.py tests/test_rc_acceptance_readiness.py \
  tests/test_invoice_owner_preview.py tests/test_mobile_media_owner_draft.py \
  tests/test_tenant_storage_integrity.py tests/test_seed_demo_tenant.py \
  tests/test_mobile_gallery_calendar_api.py -q
```

---

## What this reconciliation does **not** do

- Close GitHub issues without full acceptance proof and human confirm  
- App Store / TestFlight submission or Connect label entry  
- Invent privacy/IAP policy  
- Deploy production tenants or change billing  
- Claim device AVPlayer QA complete on Linux CI alone  
