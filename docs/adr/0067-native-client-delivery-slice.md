# ADR 0067 — Native client delivery slice (iOS Milestone 3)

**Status:** Accepted
**Date:** 2026-07-12
**Deciders:** Kevin (owner), iOS/full-stack architect

## Context

ADR 0066 established the `/api/v1` boundary and its four exact guest
principals; the checked-in iOS app then shipped as an owner read-only
companion (Milestones 1–2). A high-fidelity design handoff ("Mise Mobile")
now specifies the client-side experience: Home with dynamic next steps, a
sectioned gallery grid with a fullscreen lightbox and working favorites,
Documents with proposal/contract/invoice detail, and Bookings.

The handoff models one merged client persona. Mise's backend deliberately
has no client account — a gallery link, a client portal, a project
workspace, and a single-document link are separate capabilities with
separate PINs and separate authority (ADR 0066). Milestone 2 also shipped
gallery manifests with every media link `null` because no bearer-aware
media route existed.

## Decision

- **Map the design's client app onto the existing principals rather than
  inventing a client account.** One `ClientCompanionView` serves all four
  guest kinds; each tab renders exactly what the unlocked capability
  covers. A workspace link is the fullest experience (documents + its
  gallery + bookings); a portal link covers client-wide galleries and
  bookings; a gallery link covers that gallery and favoriting; a document
  link covers that one document. Tabs outside a capability show honest
  empty states, not upsells into wider access.
- **New reads under `/api/v1`:** `GET /client/home` (capability-shaped
  summary with server-computed next steps), `GET /client/galleries[/{id}]`
  (guest-scoped manifests reusing the owner manifest queries), `GET
  /client/bookings` (only for principals that resolve to a real
  `client_id`), and `GET /projects/{id}/proposals|contracts|invoices`
  (owner, or the exact workspace guest). Draft documents never serialize.
- **First native mutation:** `PUT/DELETE
  /galleries/{g}/assets/{a}/favorite`, gallery-guest only, keyed to the
  session's minted `visitor_id`, enforcing the existing per-section
  proofing cap with a 409 problem. Owners and workspace/portal guests have
  no visitor identity and cannot favorite — same as the web app.
- **Bearer-authenticated media:** `GET
  /media/galleries/{g}/assets/{a}/{thumbnail|preview|poster|download}`
  resolves the same on-disk derivatives as the cookie web routes, re-checks
  principal scope per request, applies published/expiry and cull delivery
  gates, and fills the previously-null manifest links with absolute URLs.
  Downloads require the gallery download scope; workspace/portal guests get
  variants only. Media gets its own generous rate bucket (`api_media`);
  original downloads share the web `download` bucket.
- **Money and legal actions stay on the web.** Accept/decline, signing, and
  Stripe checkout open each document's canonical `/p /c /i` page
  (`public_url` in the DTOs). Native e-sign and native checkout remain
  later, separately-reviewed milestones (ADR 0066, IOS-ARCHITECTURE §8).
- **Shared gallery viewer.** Owner and client render the same sectioned
  grid + lightbox components; favoriting is capability-gated in the shared
  model. The design handoff's decorative lightbox comment/download buttons
  are intentionally not shipped (its own Known Gaps flag them as visual
  only); video comments and background downloads remain M3 follow-ups.

## Consequences

- The client app degrades by capability instead of pretending a unified
  account exists; product decision #4 in IOS-ARCHITECTURE ("fund true
  client accounts?") stays open and unblocked.
- Favoriting from the app and the web converge on the same `favorites`
  rows, so proofing progress is shared across surfaces — but each gallery
  unlock (web cookie vs. app session) is a distinct visitor, matching
  existing web semantics.
- Media URLs embed the request origin; manifests are already private,
  ETag-validated responses, so origin changes simply re-derive links.
- The owner companion now renders real thumbnails through the same
  authenticated loader, replacing Milestone 2's placeholder tiles.

## Alternatives considered

- **A unified `client` principal to match the design 1:1.** Rejected —
  re-litigates ADR 0066; silently widens PIN-scoped links into cross-resource
  identity.
- **Signed/expiring media URLs instead of bearer-authenticated routes.**
  Rejected for now: credentials in query strings leak through logs/referrers,
  and the app already holds a rotating bearer session.
- **Native accept/sign/pay in this slice.** Rejected: signature evidence and
  checkout reconciliation are red-light money/legal paths; the web pages
  already do them correctly.
