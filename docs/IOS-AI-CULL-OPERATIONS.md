# Mise native owner cull operations

This runbook is the release and incident checklist for Milestone 5B.1. It covers
the native owner review deck, protected derivatives, and reversible human
keep/cut/restore decisions. It does not complete the general AI run ledger,
content-generation tools, telemetry program, APNs acceptance, or App Store release.

AI scores are suggestions. A human decision is the only action that changes client
delivery, and a cut never deletes or rewrites an original.

## 1. Safety and tenancy contract

- Every route derives the tenant from the authenticated request host. Never accept a
  caller-selected tenant, database, slug, principal, or media path.
- Reads require the exact `studio_owner` principal with `studio:read`; decisions also
  require `studio:write`. Shared-client capabilities never enter this owner surface.
- The queue is limited to ready photos in an ordinary gallery. Drops/transfers,
  videos, pending/failed assets, and cross-gallery asset IDs fail closed.
- Only exact JPEG thumbnail and preview routes are exposed. There is no owner-cull
  original/download route and bearer credentials never appear in a URL.
- Keep/cut/restore are explicit, online, server-confirmed actions. They are not
  inferred from a score and are never queued for later offline execution.
- Restore clears cull state and its decision metadata. It does not recreate a file,
  because keep/cut never removed one.

## 2. Feature flag and deploy order

`MISE_CULL_UI` defaults to `false`. It is one host-wide switch for all tenants on a
backend process: it arms the existing web cull deck, native cull routes, owner
`cull_enabled` capability, and client-delivery exclusion together. It is not a
per-tenant canary flag. Enabling it is therefore a production delivery change and
requires an explicit human release decision.

Deploy and validate the backend before distributing the iOS build. Use a separate
staging backend first, then restart the applicable web/worker processes after
changing the environment:

    MISE_CULL_UI=false

Keep production false until the backend tests, staging checks, and signed iOS build
are ready. Arm staging with `MISE_CULL_UI=true`, complete this runbook, and only then
schedule the production enablement.

The native review surface displays existing keeper and hero scores; it does not
start a scoring job. `MISE_CULL_SCORER` and `MISE_VISION_CHALLENGER_URL` are separate,
optional controls for the existing local scorer. Leave them off unless that scoring
workflow has its own validation and approval. An unscored gallery remains usable as
a manual cull queue.

## 3. API preflight

With the flag off, verify all of the following:

- owner gallery detail returns `cull_enabled=false`;
- client gallery detail always returns `cull_enabled=false`;
- cull page, derivative, and decision routes return `404`; and
- stored cut states do not filter live client delivery.

With the flag on, verify an ordinary owner gallery returns `cull_enabled=true`,
while a drop and every client manifest remain false. Exercise these exact routes:

| Method/path | Expected boundary |
| --- | --- |
| `GET /api/v1/galleries/{g}/cull?limit=25&cursor=...` | private cursor page; strong `ETag`; `If-None-Match` may return `304` |
| `GET /api/v1/galleries/{g}/cull/assets/{a}/thumbnail` | private JPEG only; strong `ETag`; no redirect |
| `GET /api/v1/galleries/{g}/cull/assets/{a}/preview` | private JPEG only; strong `ETag`; no redirect |
| `PATCH /api/v1/galleries/{g}/assets/{a}/cull` | closed keep/cut/restore body with strong `If-Match` and UUID `Idempotency-Key` |

List responses use `Cache-Control: private, no-cache` and derivative responses use
`Cache-Control: private, max-age=86400`; both use `Vary: Authorization`. A decision
response uses `Cache-Control: no-store`, returns a new item `ETag`, and may include
`Idempotency-Replayed: true` for an identical session-bound retry.

Each item also carries an opaque non-negative `media_revision`. It is stable across
cull decisions, changes when the protected stored/derivative identity changes, and
keys the iOS in-memory image cache. If media changes while a command is executing,
the backend rejects and rolls back the decision instead of accepting evidence that
the owner did not review.

The expected conflict behavior is:

- `409 pagination.collection_changed`: membership, position, or keeper-score order
  changed after page one. The app must discard the stale continuation, refresh page
  one, and never merge two queue snapshots.
- `409 resource.version_conflict`: the item changed; reload before deciding.
- `409 request.idempotency_conflict`: within one owner session, a UUID was reused for
  a different resource, action, or `If-Match`; do not retry with that key.
- `409 cull.media_changed`: protected media changed during the decision; the entire
  command is rolled back and the frame must be reloaded and reviewed.
- `422`: required header, UUID, path, cursor, limit, or body validation failed.
- `429`: honor `Retry-After`; do not spin or issue parallel page requests.

Never copy bearer tokens, opaque cursors, media URLs, filenames, client names, or
tenant identifiers into logs, screenshots, analytics, or support tickets.

## 4. Automated release gates

Focused backend gates from the repository root:

    .venv/bin/ruff check app tests
    .venv/bin/ruff format --check app tests
    .venv/bin/pytest -q \
      tests/test_mobile_cull_api.py \
      tests/test_mobile_gallery_calendar_api.py \
      tests/test_mobile_gallery_delivery_api.py \
      tests/test_mobile_api.py \
      tests/test_mobile_api_boundary.py

Before production, also run the repository's complete unit and smoke gates rather
than treating the focused suite as release proof:

    .venv/bin/python -m pytest tests/ --ignore=tests/test_smoke.py -q -m unit
    MISE_DATA_DIR=$(mktemp -d) MISE_SECRET_KEY=test MISE_ADMIN_PASSWORD=pw \
      .venv/bin/python -m pytest tests/test_smoke.py -q
    .venv/bin/ruff check .
    .venv/bin/ruff format --check .

iOS gates require macOS with current Xcode and XcodeGen:

    cd ios
    xcodegen generate
    xcodebuild test -project Mise.xcodeproj -scheme Mise \
      -destination 'platform=iOS Simulator,name=iPhone 16'

Archive Release once with the intended team, bundle ID, origin, and entitlements.
Inspect the signed product and privacy report, not only `project.yml` or source
configuration. The current non-macOS implementation environment cannot run Xcode,
so simulator tests, archive inspection, and device validation remain release gates.

## 5. Staging and physical-device acceptance

Use a dedicated staging tenant with a mix of scored and unscored photos, existing
keep/cut decisions, missing optional derivatives, and enough assets for multiple
pages. Include two tenants with overlapping numeric gallery/asset IDs to prove host
isolation.

1. Sign in as an owner and prove the action appears only when the server returns
   `cull_enabled=true`. A client capability and a drop must never show it.
2. Open the queue on iPhone and iPad. Verify score ordering, full-queue counts,
   filters, first-page cache behavior, pagination, empty/missing-image states, and
   thumbnail-to-preview presentation without loading an original.
3. During pagination, add/remove/reposition/rescore a frame. The server must return
   `pagination.collection_changed`; the app must replace the stale queue with a fresh
   page one and clearly report whether reconciliation succeeded.
4. Keep, cut, and restore one frame. Confirm the UI changes only after the server
   confirms it, the same ambiguous retry keeps its UUID, and a changed action/ETag
   receives a new intent. Exercise stale ETags from two devices.
5. After cut, test client listing, direct thumbnail/preview/original URLs, favorites,
   individual downloads, favorites/section ZIPs, the full-gallery ZIP, and the client
   portal. The cut frame must be absent or `404`.
6. Restore the frame and repeat the delivery checks. Confirm the file identity and
   derivatives did not change merely because the decision changed.
7. Replace a derivative between page load and decision. The old `If-Match` must fail,
   `media_revision` must change after reload, and no decision/audit/replay residue may
   be committed by the rejected command.
8. Go offline after a successful page load. Bounded cached metadata may remain
   visible, but no decision may be accepted or queued and cull images must not become
   protected durable downloads.
9. Expire/revoke the owner session, change workspace, and sign out. Verify requests
   stop, a terminal JSON or media authentication failure ends the same owner
   surface, private metadata is purged, in-memory media is ended, and a client
   session cannot reuse owner data.
10. Exercise VoiceOver, Dynamic Type, dark/light mode, sufficient contrast, Reduce
    Motion, keyboard/pointer use on iPad, and decision controls that do not rely on
    color alone.

## 6. Performance acceptance

Run Instruments (Memory, Network, and Time Profiler) on representative and worst
practical galleries over Wi-Fi and constrained cellular. Record the device, OS,
build, gallery size, page count, and observed peak memory; do not invent a release
SLO from simulator behavior.

Verify these implemented safety bounds under sustained grid and preview use:

- cursor pages remain at or below 100 items (the app requests 50);
- thumbnail responses are capped at 8 MiB and previews at 32 MiB;
- decoded thumbnails/previews are downsampled off-main to at most 768/4096 pixels;
- compressed authenticated media uses a bounded 48 MiB in-memory cache;
- rapid scrolling cancels obsolete work and does not cause an unbounded request
  burst, repeated `429`s, UI stalls, memory warnings, or crashes; and
- traffic contains only the cull page and protected derivative routes—never an
  owner-cull original/download request.

## 7. TestFlight rollout and observation

1. Keep production `MISE_CULL_UI=false` while an internal TestFlight group validates
   the production-signed build against staging.
2. Re-run the backend preflight on production without recording private payloads.
3. Obtain human release approval, announce the global tenant impact, enable the flag
   in a controlled window, restart, and smoke-test one approved studio.
4. Expand internal usage gradually. Because the switch is host-wide, a tenant-by-
   tenant server canary is not available in this slice.

Monitor route-template status counts and latency, `pagination.collection_changed`,
version/idempotency conflicts, `429`, `5xx`, and Apple crash/hang/memory reports.
Use only aggregate operational data; do not log request bodies, auth headers,
filenames, asset/gallery IDs, tenant hosts, cursors, or media URLs. If telemetry is
added later, reconcile the privacy manifest, App Store Connect answers, published
privacy policy, retention, and consent before shipping it.

## 8. Rollback and incident response

For a cull, tenant-isolation, auth, privacy, media, or stability incident, stop the
rollout and set `MISE_CULL_UI=false`, then restart the affected backend processes.
This immediately makes `cull_enabled` false, hides the web/native deck, returns cull
routes to `404`, and restores pre-cull behavior on live client listing/media/download
paths. Previously stored decisions and audit evidence remain; do not delete schema,
files, or audit rows as an incident shortcut.

The full-gallery ZIP reflects the cull state at its last build. A flag toggle alone
does not force an already-built ZIP to rebuild even though live paths honor the flag
immediately. When exact ZIP recovery matters, explicitly restore the affected
decisions before disabling the flag or perform the existing controlled ZIP
invalidation/rebuild procedure and verify the archive contents.

Stop TestFlight distribution or return to the prior build as appropriate, but keep
the backend flag off so cull endpoints remain inert. Revoke affected sessions for an
auth incident. Preserve privacy-safe audit evidence; never collect bearer tokens or
private media to diagnose the issue.

## 9. Release evidence still required

Record the commit, backend build, iOS build number, environment, device/OS, tester,
date, commands and exit codes, archive/privacy inspection, performance measurements,
and rollback rehearsal. Milestone 5B.1 is not release-complete until current-Xcode
generation/tests, a signed archive, real-device session/media testing, and an
internal TestFlight rehearsal have evidence.

General AI run/content features, privacy-safe production telemetry, App Store
metadata/screenshots/review, and the Milestone 5A physical APNs and production
TestFlight checks remain separate unfinished work.
