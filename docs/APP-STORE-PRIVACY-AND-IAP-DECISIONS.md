# App Store privacy + storefront/IAP — decision memo

**Status:** Evidence only. **No policy was chosen.**  
**Issues:** #179 (privacy), #180 (storefront/IAP).  
**Base audited:** `origin/main` `55e1787` + this launch-integrity branch’s code paths.  
**Date:** 2026-07-20.

This memo lists facts Kevin must decide against. It does **not** invent business decisions, edit Connect labels as final truth, submit to App Store Connect, or change billing behavior.

---

## A. Declared vs actually-collected data

### Declared today (`ios/Mise/PrivacyInfo.xcprivacy` + `docs/APP-STORE-SUBMISSION.md`)

| Category | Declared purpose | Linked | Tracking |
|---|---|---|---|
| Email address | App Functionality | yes | no |
| Name | App Functionality | yes | no |
| User ID | App Functionality | yes | no |
| Device ID | App Functionality | yes | no |

### Actually persisted / transmitted (code citations)

| Data | Where | Notes |
|---|---|---|
| Owner email + password (sign-in) | `app/mobile_auth.py` studio login | Credential; email is identity |
| Device installation UUID, name, platform, app_version | mobile auth device registration | “Device ID” category candidate |
| Session create / last-seen | `app/mobile_auth.py` session rows | **Product Interaction** candidate (retained off-device) |
| Owner task completion + audit rows | `app/mobile_owner_api.py` task check-off | Product Interaction / Other User Content candidates |
| Gallery favorites (visitor selections) | `app/mobile_client_api.py` favorites | Business data on studio server; Product Interaction candidate |
| Booking cancel / reschedule commands | mobile booking APIs + workflow tables | Product Interaction / Other User Content candidates |
| Invoice/proposal/contract status timestamps | public doc + pay routes | Client engagement state (server-side business records) |
| Media viewed over network | `app/mobile_media.py` | Not device photo library; no camera/photos permission |

**Not found:** location, contacts, IDFA/IDFV advertising IDs, on-device photo library, health, payment card entry inside the iOS binary (Stripe is web).

### Required-reason API declarations vs grep-backed use

| Declared reason | Claimed use | Actual code |
|---|---|---|
| `CA92.1` UserDefaults | last-workspace origin + installation identity | **Confirmed:** `AuthenticationCoordinator.swift` uses `UserDefaults` |
| `C617.1` file timestamps | “TenantJSONCache freshness” | **Removed 2026-07-20** from `PrivacyInfo.xcprivacy` after code audit: no file-timestamp APIs under `ios/Mise/`. `TenantJSONCache` uses application-owned `storedAt` JSON fields. |

**Engineering fix applied:** unused `C617.1` declaration removed (auditor-enforced). Remaining #179 work is still **Kevin decision** on Product Interaction / Other User Content labels — not inventable here.

---

## B. Purchase / manage-billing CTAs and flows

| Surface | CTA / link | Destination | Implementation |
|---|---|---|---|
| Auth screen | “New to Mise? Start a studio” | `{serverBaseURL}/pricing` | `AuthenticationView.swift`, `AuthenticationCoordinator.signupURL` |
| Subscription / resource recovery | “Manage billing” | tenant `manage_billing_url` / admin billing | `ResourceView.swift`; tenant descriptor fields from API |
| Tenant descriptor | `signup_url`, `manage_billing_url` | pricing + billing portal | API + `ModelDecodingTests` fixtures |
| Web SaaS | Stripe Checkout + Customer Portal | hosted subscription | `app/saas.py`, `templates/admin/saas_billing.html` |
| iOS binary | StoreKit / IAP | **none** | No StoreKit product IDs, no external-purchase entitlement files found |

**User-facing claim tension:** ADR 0070 (**Proposed**) frames a free multi-tenant companion with revenue on the hosted web subscription, while App Review Guidelines §3.1 still constrain external purchase CTAs outside entitled/allowed territories and free-companion rules (§3.1.3(f) requires *no* external purchase CTA).

---

## C. ADR 0070 / guideline tension (summary)

- ADR 0070 decides: one free App Store app; tenant at sign-in; billing on web Stripe.
- Status remains **Proposed**, not Accepted.
- U.S. storefront external-link rules are **not** a worldwide free pass for web unlock CTAs.
- Free-companion exception is incompatible with in-app “Start a studio” / “Manage billing” purchase CTAs unless those CTAs are removed or gated.
- Multiplatform features unlocked on web may also need IAP availability under §3.1.3(b) depending on classification.

---

## D. Kevin decision checklist (numbered)

1. **Privacy labels — Product Interaction:** Disclose retained session activity, favorites, task completions, and booking mutations as Product Interaction (App Functionality, linked, not tracking)? **Yes / No / Partially (list which).**
2. **Privacy labels — Other User Content:** Treat booking notes, favorites, or other user-authored business state as Other User Content? **Yes / No / Rationale.**
3. **PrivacyInfo `C617.1`:** ~~Remove unused file-timestamp reason~~ **Done (removed 2026-07-20).** Re-open only if real file-timestamp API use is added.
4. **App name / bundle / team:** Confirm Connect identity fields in `docs/APP-STORE-SUBMISSION.md` (still placeholder “Mise” in places post-Focal rebrand).
5. **Storefront territory:** Ship **U.S.-only**, **worldwide with IAP**, or **worldwide free-companion with all purchase CTAs removed from the binary**?
6. **Free-companion purity:** If free-companion, remove or hide “Start a studio” and “Manage billing” from iOS, or replace with non-purchase account management that does not link to purchase?
7. **IAP path:** If IAP required, approve StoreKit product design, pricing parity with Stripe $20/mo, and red-light money PR scope?
8. **External-purchase entitlement:** Pursue Apple’s external-purchase entitlement (where available) instead of pure web CTAs? **Yes / No / Later.**
9. **Reviewer credentials (#185):** Approve a safe hosted demo-tenant design (operator-only identity, billing-exempt, non-destructive) before App Review?
10. **Submission freeze:** Confirm no App Store submission until #179, #180, and #185 replacement are decided/implemented?

**This document does not select any option above.**

---

## E. What engineering already did (without inventing policy)

- Did **not** change privacy labels or manifest as “final submission truth.”
- Did **not** add StoreKit or alter Stripe prices/paths.
- Did contain reviewer seeding (fail-closed) and document the #185 hold.
- Shipped technical integrity fixes elsewhere on this branch (#184, #183 media) independent of App Store policy.
