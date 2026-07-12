# Mise native Content suggestions operations

This is the release, verification, retention, and incident runbook for Milestone
5B.3. It covers the owner-only native caption workspace and its optional,
asynchronous AI suggestion flow. It does not authorize approval, publication,
delivery, invoicing, payment, or any other client-facing transition.

## Safety invariant

The caption is authoritative; the provider output is not. A model call creates a
short-lived, immutable suggestion operation. It never writes the caption. Only a
later explicit owner save can copy reviewed text into a caption that is still a
draft and still has the exact version used to start generation.

- Manual draft editing works independently of the AI feature.
- Approved captions are read-only in the native app.
- The owner may edit generated text locally before explicitly saving it.
- A save keeps the caption in 'draft'; there is no native approve, publish, send,
  delivery-credit, invoice, or payment action.
- Provider failure, timeout, malformed output, session loss, expiry, or version
  conflict leaves canonical caption and financial/legal state unchanged.
- Candidate text is 'no-store' on the wire and memory-only on iOS. It is never
  written to TenantJSONCache, an offline queue, logs, analytics, or crash
  breadcrumbs.
- After explicit save, the chosen body is ordinary canonical caption content. The
  server also retains the original generated candidate in the existing AI
  provenance field so a human edit is auditable.

## Release hold and feature switches

Both switches are fail-closed and must remain off in production until every release
gate below is signed off:

~~~text
MISE_MOBILE_CONTENT_SUGGESTIONS=false
MISE_CONTENT_SUGGESTIONS_ENABLED=NO
~~~

The first switch controls server capability, creation, and worker calls. The second
is compiled into the iOS app as 'MiseContentSuggestionsEnabled'; Debug and Release
both default to 'NO'. The app shows generation controls only when the local switch
and the server's 'suggestions_enabled' capability are both true. Manual Content
reads and draft saves remain available with AI disabled.

Do not enable suggestions until all of these are verified:

1. The processor endpoint, owner, authentication, TLS, availability, and incident
   contact are documented. The configured endpoint is direct HTTPS (no HTTP,
   redirect, URL credentials, or fragment), and its certificate validates from the
   Mise runtime.
2. Contract/DPA and subprocessor records cover retention, deletion, geographic
   processing, and an enforceable no-training/no-model-improvement commitment for
   studio and client content.
3. The public privacy notice, in-app disclosure, App Store privacy answers, and
   support/deletion procedures describe the actual data flow.
4. Input/output abuse handling, moderation, operator escalation, and user-facing
   failure copy are approved.
5. Tenant daily/concurrent caps, processor-side global budget alerts/cutoff, timeout
   behavior, and a cost ceiling are exercised in staging. Tenant request quotas are
   not a global spend ceiling.
6. The processor accepts the UUID `Idempotency-Key`. Mise does not automatically
   replay an ambiguous native or web paid-call claim. Any future retry design remains
   blocked until the processor proves durable same-key deduplication.
7. Two-tenant isolation, session revocation, TTL cleanup, crash recovery, and a
   signed current-Xcode/TestFlight build pass this runbook.

Server limits are tenant-local:

~~~text
MISE_MOBILE_CONTENT_DAILY_LIMIT=10
MISE_MOBILE_CONTENT_CONCURRENT_LIMIT=1
MISE_MOBILE_CONTENT_SUGGESTION_TTL_HOURS=24
MISE_MOBILE_CONTENT_WORKERS=1
~~~

The TTL is clamped to 1–168 hours. Expired context/candidates are scrubbed on
access, before and after worker execution, and by the tenant scheduler's short
cleanup cadence. Terminal rows remain briefly as content-free quota evidence.
Hosted studio deletion scrubs all transient suggestion fields before the canonical
database is moved into its recoverable trash-retention window.

`MISE_MOBILE_CONTENT_WORKERS` defaults to one and is clamped to 1–4. Content jobs
run in this dedicated pool; slow provider work therefore cannot consume the generic
media, notification, and maintenance job pool. Keep one worker until provider cost,
tenant concurrency, and failure behavior have been exercised together. The worker
count is capacity, not a replacement for tenant-local quotas.

### Existing web button compatibility gate

Migration 085 also hardens the existing admin **Draft with AI** path. The shared
Odysseus transport now accepts only a direct HTTPS URL with a valid host, no embedded
credentials, and no fragment. Redirects are rejected before the bearer token can be
forwarded. An installation that currently uses an HTTP tailnet/LAN URL will see the
web button disabled after this code lands; do not discover that in production.

Before deploying, inventory the current endpoint without printing its token, prove
certificate trust and a non-redirecting response from the same network/container as
Mise, and run one staging web draft. Concurrent web clicks are rejected by a durable
caption claim. A timeout, cancellation, or crash after provider dispatch leaves that
claim in place even after it becomes old; the next click is blocked with an unknown
outcome and makes no provider request. Only a human may reconcile and clear it using
the identity/claim-bound procedure below. Do not claim exactly-once billing. If the
provider cannot support incident reconciliation, leave `MISE_ODYSSEUS_CAPTION_URL`
or its token unset so the web button stays dormant, and keep native suggestions off.

Odysseus currently does not report token usage or cost to Mise. `ai_runs` cost/token
aggregates are useful only for providers that supply those fields. Configure global
budget monitoring and a hard cutoff at the processor/provider account; the Mise
daily limit bounds per-tenant request volume but cannot prove or cap total spend.

## Server contract and lifecycle

All routes require the authenticated request host to select the tenant. No tenant
selector is accepted from the client.

| Route | Contract |
| --- | --- |
| 'GET /api/v1/content/captions' | exact owner read; ID-desc signed cursor; strong ETag; private revalidation |
| 'GET /api/v1/content/captions/{id}' | exact owner read; normalized detail and strong ETag |
| 'PATCH /api/v1/content/captions/{id}' | exact owner write; UUID Idempotency-Key; strong If-Match; draft body only |
| 'POST /api/v1/content/captions/{id}/suggestions' | exact owner write; versioned/idempotent 202; creates job but never changes caption |
| 'GET /api/v1/content/captions/{id}/suggestions/{uuid}' | exact requesting session only; immutable no-store operation state |

The operation state machine is:

~~~text
queued -> running -> ready | failed
ready -> applied
queued | running | ready | failed -> expired
~~~

The queued-to-running compare-and-set is the single paid-call claim. If a process
restarts after that claim, the operation becomes 'unknown_outcome'; the worker never
blindly calls the provider twice. It checks the feature switch and requesting
session before the call, then checks feature switch, session, TTL, caption existence,
and operation state again before persisting a result.

Every tenant database also has an immutable random runtime identity. A worker
captures the resolved database path and identity before outbound work, then opens
that exact existing file in read/write mode and verifies the identity plus
`offboarding=0` in the same transaction as its final ledger/result write. A deleted
tenant, replaced file, or changed recovery path therefore cannot receive a late result
from the old tenant.

Session revocation atomically removes context, candidate, provider, and model and
detaches the operation. Caption deletion cascades the operation. An outbound call
already in flight cannot be canceled synchronously after logout/deletion, but its
result cannot be persisted or exposed.

Hosted deletion closes admission before revoking sessions: it sets the tenant-local
offboarding marker, scrubs transient operations, marks the control row deleted and
canceled while retaining the original slug, permanently records that slug in
`retired_tenant_slugs`, and queues every observed platform subscription before any
Stripe call. It then attempts each unclaimed cancellation once and parks storage under
the control row's internal `tombstone_slug`; the public `slug` remains the original
address for audit only and deleted rows are never routable. The original slug is
permanently unassignable, including during recovery. A retry finishes an interrupted
park; a failure before the control-plane deletion commit restores normal admission.
Never clear `offboarding` or delete a retired-slug record merely to make a failed
deletion disappear.

Offboarding runs outside the async request loop, but its secure scrub, `VACUUM`, and
WAL checkpoint can still take time, hold SQLite locks, and require substantial free
space/temp capacity (plan for roughly another database-sized working copy plus
headroom). Verify capacity before deletion and alert on failure; the route must fail
closed rather than park a partially compacted database.

The native provider context is intentionally minimal: bounded caption label, period,
and the owner's optional instruction. Client contact data, private caption notes,
existing body text, tenant selectors, credentials, and business/financial state are
not sent.

## Migration 085

Migration '085_mobile_caption_suggestions.sql':

- adds monotonic caption revision/update metadata, an opaque caption identity, and
  the existing web route's durable generation-claim fields;
- creates the immutable database identity and tenant-local offboarding barrier;
- creates the session-bound suggestion/job table with closed states and payload
  consistency checks;
- enforces at most one queued/running suggestion per caption; and
- installs session-deletion scrubbing.

This is a red-light production migration. Before deploy, take and verify a restore
of the tenant databases. Deploy the migration/backend first with the server switch
off. Never run the rollback while new application code is live: session revocation
expects the new table. A rollback requires the switch off, jobs settled, a verified
backup, compatible application code restored, and then
'migrations/rollback/085_mobile_caption_suggestions.sql'.

Rollback 085 owns an explicit `BEGIN IMMEDIATE` transaction. It removes the tables,
indexes, triggers, caption columns, and its `schema_migrations` marker in that same
transaction. Any failed step rolls the whole rollback back. A successful rollback is
therefore intentionally re-applicable: a later `db.migrate()` sees no marker and
recreates the complete schema with a new database identity. Verify either the fully
rolled-back or fully-applied shape; never hand-edit only the marker.

## Retention, backups, and physical-remanence limits

Live connections use `PRAGMA secure_delete=ON`. Cleanup clears provider input/output
logically and then attempts `PRAGMA wal_checkpoint(TRUNCATE)`. A busy reader can pin
older WAL frames, so TTL/logout cleanup is not a promise of instantaneous forensic
erasure from the live filesystem. The warning `mobile caption cleanup WAL checkpoint
remains busy` means the logical scrub committed but WAL truncation must be retried.
Host snapshots, filesystem snapshots, and older off-site objects have their own
retention and deletion lifecycle.

Backup code copies with SQLite's backup API. For each tenant/parked-tenant destination
that has suggestion state, it scrubs transient suggestion/session fields, commits, and
`VACUUM`s before compression; the control DB has no such table. Queued/running/ready/failed
operations in a restored tenant snapshot are
content-free `failed/session_ended` records and are never resumed. Hosted media sync
excludes every live `mise.db`, `mise.db-wal`, and `mise.db-shm`; only completed
sanitized `*.db.gz` snapshots and health markers leave through the backup tree.
Raw/gzip work is staged on the destination filesystem outside the sync allowlist and
only the verified archive is atomically installed. Hosted and single-tenant backup
passes take a non-blocking exclusive file lock, so a timer and manual run cannot
write/prune/sync concurrently; lock contention fails/skips loudly. Deleted studios
receive a
separate sanitized database archive under the snapshot's `trash/` directory; their
parked media remains in the tenant media sync.

Before the first enablement, create and restore-check a fresh sanitized backup, then
list the entire configured remote plus its `*-history` trees. Older releases may
have uploaded raw DB/WAL/SHM files, plaintext/partial staging files, or unsanitized
archives. The new exclusions do not prove those historical objects were deleted.
With explicit human approval, purge them or apply a documented remote lifecycle,
then re-list and record that no prohibited objects remain. Also set and periodically
exercise retention for rclone history; it is versioned but not automatically bounded.

## Automated preflight

From the repository root:

~~~bash
.venv/bin/python -m pytest \
  tests/test_mobile_content_api.py \
  tests/test_caption_ai.py \
  tests/test_mobile_api.py \
  tests/test_providers.py \
  tests/test_mobile_auth.py \
  tests/test_smoke_ai_runs.py \
  tests/test_hosted_backup.py \
  tests/test_jobs_bulkhead.py \
  tests/test_saas.py -q
.venv/bin/python -m pytest tests/ --ignore=tests/test_smoke.py -q -m unit
MISE_DATA_DIR="$(mktemp -d)" MISE_SECRET_KEY=test MISE_ADMIN_PASSWORD=pw \
  .venv/bin/python -m pytest tests/test_smoke.py -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
git diff --check
~~~

On macOS, generate the project and run 'xcodebuild test' exactly as documented in
'ios/README.md'. Linux static review is not release evidence.

## Staging API checks

Use non-production content and a test provider account.

1. Verify an owner can list/detail captions; a guest receives 403, an absent token
   receives 401, and a token replayed on another tenant host receives 401.
2. Verify list/detail 200 and 304 headers, signed pagination, duplicate/unknown
   query rejection, and overlapping tenant-local caption IDs.
3. With both switches off, confirm manual editing works, generation controls are
   absent, the generation route returns 404, and no command/job/audit/provider
   call is created.
4. Start generation with a current strong ETag. Confirm 202, Location,
   'Cache-Control: no-store', one command, one job, and one content-free audit row.
5. Retry the identical request concurrently with the same UUID. Confirm both
   responses match, one is marked replayed, and only one paid operation exists.
6. Reuse the UUID with different input and use stale, weak, wildcard, or missing
   If-Match values. Each must fail without canonical mutation.
7. Exercise disabled, timeout, malformed, oversized, and control-character provider
   results. Only closed safe failure reasons may reach the app.
8. Revoke the requesting session while queued and while the provider is in flight.
   Confirm transient fields are scrubbed and the candidate is never retrievable.
9. Let a ready operation expire without polling. Confirm the periodic sweep clears
   context, candidate, provider, and model.
10. Change or approve the caption before save. Confirm the suggestion becomes stale
    and cannot overwrite the new version.
11. Save a locally edited suggestion. Confirm one revision increment, status remains
    draft, transient operation output is scrubbed, and no delivery/invoice/payment
    row changes.
12. Inspect response keys and logs for prompt/context, raw provider/model/error,
    credentials, generated text outside the candidate field, and authorization or
    `Idempotency-Key` header fields. None may appear as logged headers/values. An
    accepted opaque suggestion UUID may appear in its response/URL and ordinary
    access-log path; treat it as bounded identifier metadata, not as content or a
    bearer credential, and apply the access log's normal restricted
    retention/redaction policy.
13. Block the content provider and saturate the content pool. Confirm an unrelated
    generic job still runs, then release the provider and confirm the content job
    reaches one safe terminal state.
14. Start a request/worker, set offboarding or replace the database path with a
    different database, then release it. Confirm no provider result or ledger row is
    written to either replacement/live tenant and no new mobile session is issued.
15. Exercise the existing web button twice concurrently and across a stale claim.
    Confirm both conditions make no additional provider call; the stale claim remains
    blocked as an unknown outcome until the human reconciliation procedure is used.

## iPhone and iPad acceptance

- Open Content from Home, the compact More destination, an iPad sidebar, and a
  validated '/app/content/captions/{id}' route.
- Verify cache-first list/detail display, age/offline state, first-page 304
  revalidation, pagination bounds, search, and draft/approved filtering.
- Confirm approved captions are read-only and no approval/publication action exists.
- Generate with a short optional instruction. A same-process ambiguous retry must
  reuse the exact request and idempotency key. After the server accepts and returns
  an operation UUID, relaunch may resume through the persisted opaque handle.
- Terminate before a 202 is received. Confirm the app persisted no instruction,
  source ETag, or pre-accept key and does not silently replay the prompt. This narrow
  crash window is intentionally safe rather than automatically recoverable.
- Confirm generated text appears separately from the editor. Use suggestion copies
  it locally only; Discard removes it from memory; only Save performs a server
  mutation.
- Force a 409 from another device. The editor and reviewed suggestion must remain
  visible so the owner can copy/reconcile; the app must not silently reload over
  local work.
- Load a candidate, terminate the app, and inspect protected cache files/backups.
  Candidate text and optional instruction must not be present.
- Sign out or remotely revoke the session during refresh, polling, and save. No late
  response may recreate cache or expose a candidate.
- Test Dynamic Type, VoiceOver, Bold Text, Increase Contrast, Reduce Motion,
  light/dark mode, split view, rotation, and the oldest supported iPhone/iPad.

## Monitoring with content-safe evidence

Never log caption text, instruction, context, candidate, raw provider/model/error,
bearer/session values, or request `Idempotency-Key` headers. Accepted opaque operation
UUIDs are permissible ordinary route/access-log metadata unless deployment policy
redacts them; do not use them as high-cardinality metric labels or combine them with
content. The implemented content-safe process messages are sufficient for alerting:

~~~text
mobile caption suggestion reached safe terminal state (reason=<closed-code>)
mobile caption suggestion quota denied (kind=daily|concurrent)
mobile caption suggestions expired and scrubbed (count=<n>)
mobile caption cleanup WAL checkpoint remains busy
caption AI generation has an unknown prior outcome
caption AI generation outcome is unknown
caption AI generation cancelled with outcome unknown
~~~

For a tenant database selected through the normal tenant runtime, these read-only
queries provide aggregate evidence without selecting payload columns:

~~~sql
SELECT status, COALESCE(failure_code, 'none') AS reason, COUNT(*) AS total
  FROM mobile_caption_suggestions
 GROUP BY status, COALESCE(failure_code, 'none');

SELECT COUNT(*) AS expired_not_scrubbed
  FROM mobile_caption_suggestions
 WHERE status IN ('queued','running','ready','failed')
   AND expires_at <= datetime('now');

SELECT status, COUNT(*) AS total
  FROM jobs
 WHERE kind='mobile_caption_suggestion'
 GROUP BY status;
~~~

Alert on any sustained `unknown_outcome`, provider/internal failure increase,
non-zero `expired_not_scrubbed` beyond two configured cleanup cadences, repeated busy
checkpoint warnings, quota-denial bursts, or growing queued/running depth. Correlate
using time windows and request IDs already emitted by HTTP access logs, not content.
Provider latency/cost/token metrics exist only when the adapter reports them; Odysseus
currently does not report cost or tokens, so processor/account budget monitoring is
mandatory. Do not dump provider/model columns into incident chat or metrics labels.

## Human reconciliation for a stuck web claim

Never clear `ai_claim_token` just because `ai_claimed_at` is old. First set native
suggestions false and, when the web path must stop, unset the shared provider URL/token
and restart. Identify the tenant by immutable control-row id
(not by display name), and reconcile the claim UUID with the provider through an
approved secure channel. Determine whether the request was absent, still running,
completed, or billed; retain only content-free incident evidence. A completed result is
still a draft suggestion and must not be copied into canonical content by an operator.
Wait for/stop provider work and resolve billing before clearing. If the provider cannot
give an authoritative answer, leave the claim closed and escalate.

In a maintenance window, open the verified tenant DB locally and run the following as
one transaction. Substitute values captured from that same row locally; never paste the
claim/identity into logs, chat, or shell history. The database-identity predicate prevents
clearing a lookalike row in a replaced database, while caption identity, revision, and
claim predicates prevent clearing newer work.

~~~sql
BEGIN IMMEDIATE;

SELECT database_identity, offboarding
  FROM mobile_runtime_state
 WHERE singleton=1;

SELECT id, plan_id, revision, identity_token, ai_claim_token, ai_claimed_at
  FROM retainer_captions
 WHERE id=<caption-id>
   AND plan_id=<plan-id>
   AND ai_claim_token IS NOT NULL;

UPDATE retainer_captions
   SET ai_claim_token=NULL, ai_claimed_at=NULL
 WHERE id=<caption-id>
   AND plan_id=<plan-id>
   AND status='<observed-status>'
   AND revision=<observed-revision>
   AND identity_token='<observed-caption-identity>'
   AND ai_claim_token='<reconciled-claim-uuid>'
   AND ai_claimed_at='<observed-claimed-at>'
   AND EXISTS (
       SELECT 1 FROM mobile_runtime_state
        WHERE singleton=1
          AND offboarding=0
          AND database_identity='<observed-database-identity>'
   );

SELECT changes() AS must_equal_one;
~~~

If the observed `ai_claimed_at` is NULL, use `AND ai_claimed_at IS NULL` instead of
the equality predicate; never omit the timestamp guard.
Stop after `SELECT changes()`. Run `COMMIT;` separately only when the result is exactly
one; otherwise run `ROLLBACK;` and escalate. After commit, re-read the row, keep both
the native server switch and web provider endpoint dormant until the incident is closed,
and require the owner to start any replacement draft explicitly. Do not delete
`ai_runs`, provenance, or the tenant's retired-slug/runtime-identity records.

## Rollout and incident response

Deploy backend/migration with suggestions off, run two-tenant staging, then ship an
internal TestFlight build with the iOS switch off. Enable only for an internal test
tenant after the HTTPS/idempotency and historical-backup purge gates, observe a full
TTL window, then expand deliberately.

For a privacy, tenant-isolation, cost, moderation, or provider incident:

1. Set 'MISE_MOBILE_CONTENT_SUGGESTIONS=false' immediately.
2. Stop TestFlight expansion and preserve request IDs plus aggregate evidence only.
3. Revoke affected sessions; allow the cleanup sweep to scrub transient rows.
4. If needed, disable the affected iOS build remotely through the server capability;
   manual caption reads/edits remain available.
5. Re-run isolation, retention, one-attempt, cache inspection, and physical-device
   checks before re-enabling.

Do not delete audit/provenance or alter canonical captions as an automatic incident
response. Any content deletion must follow the studio's normal authenticated data
workflow.

## Recovering a parked hosted database

Recovery is an operator action, not an automatic undo. Stop the app and backup
sidecar first, take verified copies of the control DB and parked directory, and
identify the control row by immutable tenant id. The original slug is permanently
retired and must remain in `retired_tenant_slugs`. Choose a distinct human-approved
recovery slug, confirm it is neither active nor retired, and confirm its target
directory does not exist. There is no original-slug exception. Never bulk-delete
reservations or disturb an address already assigned on a legacy pre-reservation
installation.

1. While the app is stopped, bind that same control-row tenant id to the chosen recovery
   slug and clear `deleted_at` in one explicit control-DB transaction. Do not remove
   or transfer the original `retired_tenant_slugs` row. Reconcile Stripe first and
   set `plan_status` only to the human-verified billing truth;
   recovery must not silently restart a subscription or grant paid access. Keep the
   custom domain unset until it is verified again.
2. Restore/move the matching parked directory to exactly
   `SAAS_TENANT_DATA_DIR/<approved-slug>/`. Refuse symlinks, identity mismatches, an
   existing destination, or a failed `PRAGMA quick_check`. Remove no WAL/SHM from a
   running database; the app is still stopped for this step.
3. Open that restored `mise.db`, run `BEGIN IMMEDIATE`, verify there is exactly one
   well-formed `mobile_runtime_state` row with the expected database identity, then
   set `offboarding=0`, update its timestamp, and commit. This is deliberately last:
   mobile admission must not reopen while control-plane/path recovery is partial.
4. Start the stack, verify host-to-tenant-id routing and health, and require a new
   owner login. Offboarding revoked the old API sessions and scrubbed operations;
   recovery must not resurrect them. Any restored in-flight suggestion remains
   content-free `session_ended` and must be generated again by an explicit owner
   action.

If any assertion fails, keep the app stopped, leave `offboarding=1`, restore the
verified backups, and escalate. Do not override original-slug retirement under any
circumstance, and never copy a parked database into another tenant.
