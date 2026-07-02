# ADR 0062 — Security hardening Slice 2: tenant & client data isolation

**Status:** Accepted (production hardening — pre-beta security loop, slice 2 of 5)
**Date:** 2026-07-02
**Deciders:** Kevin (owner), security-focused architect

## Context

Slice 2 audited whether an authenticated request can reach data outside its lane. The
architecture's physical isolation (instance-per-tenant DB + media root via
`tenant_runtime`, ADR 0039/0048) holds up well; most of the audit **verified existing
correctness** rather than finding holes. One real hosted-mode defect surfaced, of the
same class ADR 0048 fixed for admins.

### Verified correct (locked with regression tests, no change needed)
- **Media serving** (`/media/{slug}/{variant}/{asset_id}`): asset bound `id AND
  gallery_id`, path built from a server-generated uuid filename (no client path input,
  no traversal), gated by `require_visitor`. Cross-gallery/cross-tenant id → 404.
- **Portal file serving**: every asset bound to `portal.client_id` via JOIN; brand files
  `WHERE id=? AND client_id=?`; the `ratio` URL token validated against active presets.
- **Gallery visitor cookie**: carries a random server-side token
  (`secrets.token_urlsafe`) looked up `WHERE token=? AND gallery_id=?` in the
  tenant-scoped DB. Global `SECRET_KEY` makes the *signature* validate cross-tenant, but
  the random token exists only in the minting tenant's DB → a replayed cookie finds no
  row → 403. Stronger than a self-contained claim; no change.
- **PII in logs**: log lines carry ids/counts, never raw client email/name values.

### The defect
Portal and workspace **client sessions** validated a self-contained signed claim —
`unsign(cookie) == "portal:<id>"` / `"workspace:<id>"` — with **no tenant binding**.
Portal/project ids restart per tenant and `SECRET_KEY` is global, so a `portal:5` cookie
minted on studio A validates against studio B's portal 5 (signature valid, payload
equal), exposing a client's galleries, contracts, invoices, brand assets, and PII across
studios. Host-only cookies stop the browser from auto-sending it, but a manual copy
defeats that — exactly the threat ADR 0048 closed for the admin cookie, left open for the
two client session types.

## Decision

Bind client-session cookies to the serving studio via `security.client_session_payload`:

- **Single-tenant:** ``"<kind>:<id>"`` — byte-for-byte the legacy claim, so self-hosted
  client cookies keep working with no forced re-PIN.
- **Hosted:** ``"<kind>:<tenant_id>:<id>"``. The immutable tenant **id** (not the reusable
  slug — ADR 0051) means a deleted-and-reclaimed slug can't resurrect a stale session.

Both `portal.py` and `workspace.py` mint and check through the helper. A copied
`portal:7:5` cookie no longer equals studio 8's expected `portal:8:5`.

## Consequences

- Cross-tenant client-portal/workspace replay is closed, consistent with the admin fix.
- Hosted client sessions minted before this ship are invalidated once (clients re-enter
  their PIN) — acceptable pre-beta; single-tenant self-hosters see no change.
- Regression tests now assert the cross-tenant denial and the single-tenant-unchanged
  payload, so a future refactor can't silently reintroduce the class.
- Green-light: one helper + four call sites, no schema, no money path.

## Alternatives considered
- **Server-side session tokens for portal/workspace** (like the visitor cookie). Cleaner
  long-term, but a heavier change (new tables, lookups) than binding the existing claim;
  deferred. The tenant-id-bound claim closes the vulnerability now.
- **Slug instead of tenant id in the payload.** Rejected — reusable after delete (ADR
  0051); the id is immutable and globally unique.
