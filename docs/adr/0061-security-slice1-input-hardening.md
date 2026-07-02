# ADR 0061 — Security hardening Slice 1: attack surface & input validation

**Status:** Accepted (production hardening — pre-beta security loop, slice 1 of 5)
**Date:** 2026-07-02
**Deciders:** Kevin (owner), release hardening engineer

## Context

A pre-beta security review of the upload, PIN, and public-intake surfaces found four
input-validation gaps. None was catastrophic on its own (the global
`X-Content-Type-Options: nosniff`, `X-Frame-Options`, CSP, and CSRF middleware were
already in place), but each is commercially material for a paid multi-tenant product.

## Decision

**1. Per-file upload ceiling + cleanup.** Both upload routes (gallery media
`app/admin/uploads.py`, bookkeeping receipts `app/admin/financials.py`) streamed
`await f.read()` with **no byte ceiling** — a hostile or buggy client could stream
unbounded bytes into one file and fill the shared volume, which in hosted mode is a
cross-tenant denial of service. New `app/upload_guard.save_capped` stops and **deletes
the partial file** the instant the running size crosses `MAX_UPLOAD_MB` (media, default
2 GB) / `MAX_RECEIPT_MB` (default 25 MB), returning 413. The receipt path was **also
missing the `MIN_FREE_GB` disk-floor check** the gallery path had — added. (A per-tenant
storage *quota* is the complete answer, tracked post-beta; this is the per-file backstop.)

**2. Content-disguise rejection.** Uploads were accepted on **extension alone**. The
guard now rejects, on the first chunk, any file whose leading bytes are markup/script
(`<html`, `<script`, `<svg`, `<?xml`, …) under any media extension, and requires
well-known image/PDF signatures (JPEG/PNG/GIF/TIFF/WEBP/PDF) to match their extension.
Video/HEIC containers vary too much to allowlist, so they clear the markup gate only
(ffmpeg/imaging is their real validator). Defense in depth behind `nosniff`.

**3. Constant-time PIN comparison.** All three PIN gates (gallery, portal, workspace)
used `pin.strip() != stored` — a short-circuiting compare that leaks PIN length and
prefix through response timing. New `security.pin_matches` uses
`secrets.compare_digest` behind a length pre-check (which only ever *rejects*). PIN
lockout/throttle was already sound and per-visitor (ADR 0058).

**4. Email header-injection hardening.** Public contact/lead forms pass an
attacker-supplied address into `mailer.send(..., reply_to=<their email>)`. `mailer`
now strips CR/LF and control chars from `To`/`Reply-To`/`Subject` header values — the
classic SMTP header-injection (smuggled `Bcc:`/headers) vector. The modern
`EmailMessage` policy already rejects most of this at send time; stripping first is
defense in depth and turns a hard 500 into a clean send.

## Consequences

- A single upload can no longer exhaust the shared disk; a disguised markup file is
  refused at both upload and (already) at serve time.
- PIN gates leak no timing signal; the change is invisible to legitimate clients.
- Lead/contact email can't be used to smuggle headers.
- Backward-compatible: default caps are generous (real RAW/video unaffected); legit
  exotic media still uploads; single seam per concern, all covered by unit tests.
- Green-light: no schema, no money path, no auth-model change — pure input validation.

## Alternatives considered

- **Full MIME allowlist / libmagic on every byte.** Rejected — heavy dependency and
  false-rejects exotic-but-legit media; the markup-reject + known-signature check blocks
  the actual attack (markup served from the media origin) without that cost.
- **Hashing gallery PINs at rest.** Deferred — 4-digit, human-shared, low-value PINs;
  hashing them complicates the operator's "read a client their PIN" flow for marginal
  gain. Documented, revisit if PIN entropy increases.
- **Per-request total upload cap.** Deferred to the per-tenant storage quota (post-beta);
  the per-file cap + disk floor is the high-impact minimal guard for now.
