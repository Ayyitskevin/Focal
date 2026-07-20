# Launch integrity matrix — Focal

**Branch tip base:** `origin/main` `55e1787` (Focal rebrand #201 + gallery paging #200 + storage fail-loud #198 + seeder containment #195).  
**This branch:** `grok/launch-integrity` — engineering fixes for #184 / remaining #183 + decision memos for #179 / #180.  
**Date:** 2026-07-20.

Disposition legend:

| Disposition | Meaning |
|---|---|
| **fixed-on-main** | Merged to `main` before this branch; verified still holds |
| **fixed-this-branch** | Implemented and tested on `grok/launch-integrity` |
| **still-open engineering** | Remaining code work (not blocked on policy) |
| **owner decision** | Requires Kevin product/legal/policy choice |
| **stale-open** | Tracker still open but acceptance is already shipped; close with evidence |

| Issue | Title | Disposition | Evidence |
|---|---|---|---|
| **#179** | App Store privacy manifest and label are not submission-accurate | **owner decision** | Manifest + submission pack still declare only email/name/userID/deviceID and `C617.1` file timestamps. Code evidence: `ios/Mise/PrivacyInfo.xcprivacy`, `docs/APP-STORE-SUBMISSION.md`, `TenantJSONCache.storedAt` is app-owned JSON (no file-timestamp APIs found). Product Interaction / Other User Content not decided. See `docs/APP-STORE-PRIVACY-AND-IAP-DECISIONS.md`. |
| **#180** | Decide storefront/IAP strategy before shipping iOS web-purchase CTAs | **owner decision** | Web CTAs live: “Start a studio” → `/pricing` (`AuthenticationView` / `AuthenticationCoordinator`); “Manage billing” (`ResourceView`). No StoreKit. ADR 0070 still **Proposed**. See decision memo. |
| **#181** | Missing tenant database can be silently recreated as empty studio | **fixed-on-main** | PR #198 (`63d3cc8`) + claim #197. Regression: `tests/test_tenant_storage_integrity.py` green on this branch tip. |
| **#183** | Native gallery viewer breaks owner drafts, video playback, large galleries | **fixed-this-branch** (paging was already on main) | **Paging:** PR #200 (`9002100`) — `assets_has_more` / `assets_next_cursor`, page ≤ 100; `tests/test_mobile_gallery_calendar_api.py`. **Owner draft media:** `app/mobile_media.py` owner bypass of publish/expiry gate; `tests/test_mobile_media_owner_draft.py`. **Video still/playback:** `GalleryMediaPresentation` + `AuthenticatedRemoteVideo` (poster still, AVPlayer for MP4); structure + Swift unit tests. Guest publish/expiry unchanged. Residual: full AVPlayer visual QA on device (env limit on Linux). |
| **#184** | Owner invoice preview marks the invoice as viewed by the client | **fixed-this-branch** | Public path: `_record_client_first_view` skips admin session + one-time `status='sent'` gate (`app/public/pay.py`). Native AR: in-app “Preview invoice” DisclosureGroup — **no** `Link` to `publicURL` (`CommercialView.swift`). Client anonymous GET still flips `sent → viewed` once. Tests: `tests/test_invoice_owner_preview.py`. |
| **#185** | Merged reviewer-demo seeder remains held; do not run on hosted state | **still-open engineering** (containment fixed; replacement design held) | Containment: #188 (`bbeef65`) + #194/`#195` (`42106d1`). `scripts/seed_demo_tenant.py` is a fail-closed tombstone (no app/DB import). Full safe replacement (operator identity, billing exemption, non-destructive convergence) still needs human-approved design — **do not claim App Review ready**. |
| **#186** | Make client navigation capability-aware | **stale-open** (implementation merged; tracker not closed) | PR #191 (`2088b62`) + claim #190. Capability docs #192/`#193`. Tests: `tests/test_ios_client_access_docs.py`, iOS `ClientAccessPolicyTests` / `ClientDestinationGateTests`. Recommend close with evidence after human confirm. |
| **#192** | Align native client capability docs with ADR 0067 | **fixed-on-main** | PR #193 (`f725ecd`). Closed on tracker. |
| **#194** | Remove remaining unsafe reviewer-demo guidance + harden tests | **fixed-on-main** | PR #195 (`42106d1`). Closed on tracker. Docs point at #185 hold. |
| **#199** | Paginate native gallery assets without whole-manifest failure | **stale-open** (implementation merged; tracker not closed) | PR #200 (`9002100`). Parent #183 paging slice. Recommend close after human confirm; does not alone close #183. |
| **#201** | Rebrand Mise as Focal | **fixed-on-main** | PR #201 (`75dad7d`). Runtime still uses many `MISE_*` identifiers by design (out of scope). |

## Adjacent holds (not reopened here)

| Item | Notes |
|---|---|
| T3 / App Review credentials | Blocked on #185 replacement design |
| Full native “pocket studio OS” parity | #182-class; out of scope |
| ADR 0070 | Still Proposed — product decision for #180 |
| Production / flow deploy | Separate repo `kleephotography`; not this sandbox |

## What this branch does **not** do

- App Store / TestFlight submission or Connect label entry
- Choosing IAP vs free-companion vs U.S.-only storefront
- Self-merge of red-light money path (invoice lifecycle) — PR only
- Re-enable reviewer seeding on hosted state
- Touch production tenants or flow `/opt/mise`
