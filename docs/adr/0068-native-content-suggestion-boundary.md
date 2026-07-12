# ADR 0068 — Native caption suggestions are immutable, session-bound operations

**Status:** Accepted (implementation staged; production provider release gate open)

**Date:** 2026-07-11

**Deciders:** Kevin (owner), iOS/full-stack architect

## Context

The owner app needs a native Content workspace and optional caption assistance.
Existing web drafting performs an outbound call inside a request and then writes the
caption. Reusing that shape on mobile would make retries, app termination, session
revocation, and concurrent edits ambiguous. Generated client-facing copy also needs
a stricter privacy and human-review boundary than ordinary cached reads.

The retainer caption is the source of truth. Approval and delivery credit are
separate human workflows, and invoices/payments must remain entirely unrelated.

## Decision

- Native list/detail and manual body editing do not depend on an AI provider.
- Suggestion generation is default-off on server and iOS. Enabling it requires the
  processor/privacy/cost/TestFlight checklist in
  'IOS-CONTENT-SUGGESTIONS-OPERATIONS.md'.
- A request creates a durable queued operation bound to the exact owner API session,
  caption identity, and base revision. It never mutates the caption.
- Queued-to-running is the one paid-call claim. Restart after that claim yields
  'unknown_outcome', never an automatic second call.
- Content jobs run in a dedicated, bounded worker pool (one worker by default,
  clamped to 1–4) so a slow paid provider cannot consume the generic media,
  notification, or maintenance pool. Tenant-local daily/concurrent quotas still apply.
- The worker rechecks feature switch and session before outbound work and rechecks
  feature switch, session, TTL, caption existence, and operation state before
  storing a result.
- Every tenant database owns an immutable runtime identity and offboarding marker.
  Outbound work captures the resolved path/identity; a final result/ledger write opens
  that exact existing file and verifies identity plus open admission in the same
  transaction. Hosted deletion closes admission before revoking/scrubbing,
  permanently records the original slug as retired, and holds the live path until
  the directory is parked. Routine retention never removes the slug reservation.
- Candidate output is immutable and short-lived. It uses 'no-store' on the API,
  remains memory-only in iOS, and is scrubbed on apply, expiry, revocation, and
  periodic cleanup.
- Only a separate strong-ETag/idempotent PATCH may save reviewed text. It requires a
  still-draft caption and, when a suggestion is cited, the same session/base
  revision. The owner may edit the candidate before save.
- A save remains 'draft'. The native API has no approve, publish, send,
  delivery-credit, contract, invoice, or payment command.
- The provider receives only bounded label, period, and optional instruction.
  Provider/model/error/context/idempotency details never enter the mobile DTO.
- The shared provider transport accepts only direct HTTPS with no URL credentials or
  fragment and refuses redirects before bearer forwarding. Production remains off
  until endpoint certificate/no-redirect behavior and UUID-header acceptance are
  verified. Durable provider-side idempotency is required before any future replay,
  not asserted by the current no-replay design.
- After explicit save, the chosen body is canonical business content and the
  original candidate remains in server-side AI provenance for auditability.

## Consequences

Positive:

- Native process restarts after the paid-call claim do not make another provider call
  and cannot silently overwrite human work. This is not a claim that an external
  provider offers exactly-once billing.
- Session revocation and TTL enforce a content-free logical scrub; physical-remanence
  limits are called out below.
- Manual Content management can ship while provider readiness remains blocked.
- The app offers native review without granting a model publication or financial
  authority.

Costs and limitations:

- Migration 085 adds caption revisions/identity, the existing web route's durable
  generation claim, immutable database runtime state, and an operation table. Its
  atomic rollback removes its migration marker so a later migrate can reapply it.
- Jobs gain a dedicated content executor and the scheduler gains one cleanup pass.
- An already in-flight external request cannot be synchronously canceled after
  logout or caption deletion; its result is discarded and cannot become visible.
- Pre-accept prompt/key state is intentionally unavailable after process loss;
  accepted opaque operation handles may resume without persisting generated text.
- Applying a candidate retains server-side provenance and therefore follows the
  caption's normal authenticated data-retention/deletion workflow.
- `secure_delete` plus a best-effort truncate checkpoint does not promise immediate
  forensic erasure from a busy live WAL, filesystem snapshots, or historical backup
  objects. Backup destinations are scrubbed and `VACUUM`ed; live DB/WAL/SHM files are
  excluded from hosted media sync, and parked databases receive sanitized archives.
- The existing synchronous web button uses a durable per-caption claim. After
  provider dispatch, transport failure/cancellation/crash leaves the claim in place;
  age never causes an automatic replay. A human must reconcile provider outcome and
  billing, then clear exactly the identity- and claim-bound row. Future automatic
  retry remains rejected unless durable provider idempotency is proven.
- Odysseus currently reports no token/cost figures to Mise. Tenant daily quota limits
  request volume, not global spend; processor/provider-account budget monitoring and
  cutoff remain release requirements.

## Rejected alternatives

- Direct provider write into the caption: retries and concurrent human edits could
  clobber canonical content.
- Synchronous mobile generation: request lifetime and app termination make
  outcome/retry state ambiguous.
- Automatic retry after a running crash: the provider call may already have been
  billed or completed.
- Treating a repeated `Idempotency-Key` as proof of exactly-once provider work: only
  the provider can enforce and document that property.
- Persist candidate output in the device cache: offline convenience does not justify
  retaining generated client content at rest.
- Let model success approve/publish/deliver: AI output is assistive content requiring
  explicit human review.
