# Mise native AI activity operations

This is the release, verification, and incident runbook for Milestone 5B.2. It
covers the owner-only, read-only AI provenance feed. It does not authorize a model
call, expose generated content, approve a draft, or change studio/client state.

## Safety invariant

The native boundary exposes normalized operational metadata, not a serialized
`ai_runs` row. A response may contain only:

- run ID; normalized capability, status, and review requirement;
- a closed, normalized provider family (never the raw provider or model columns);
- validated latency, tokens, and integer micro-USD when reported;
- a generic server-owned subject class with no resource ID or client title; and
- the run timestamp.

It must never contain raw provider errors, correlation or idempotency values,
prompts, outputs, captions, validation text, filenames, paths, endpoints, provider
run/job IDs, review URLs, payload fragments, or credentials. Raw errors are not made
safe by truncation: upstream bodies and exception strings can contain personal data,
internal topology, filesystem paths, or secrets.

The feed is advisory and read-only. No status means that content was approved,
published, delivered, billed, or applied.

## Server contract

| Route | Contract |
| --- | --- |
| `GET /api/v1/ai/runs` | exact owner read; ID-desc cursor page; limit 1–100 |

The authenticated request host is the only tenant authority. Cursors are signed and
tenant-bound. Every page reauthenticates. A `200` uses `private, no-cache`,
`Vary: Authorization`, and a strong `ETag`; `If-None-Match` may return `304` with
the same private headers. There is intentionally no detail route, server filter, or
write command in this milestone.

The ETag carries a projection version. Any future change to redaction,
normalization, or wire semantics must bump `_REPRESENTATION_VERSION`; otherwise a
page-one `304` could preserve older cached pages assembled under prior rules.

The app requests up to five 100-run pages. It stores only a fully validated refresh
under `ai-activity.v1` in the protected tenant cache. Failure on any page leaves the
previous snapshot intact. A continuation after page five sets the local
`hasOlderRuns` disclosure; it is not fetched. Terminal authentication, workspace
identity change, logout, or observed session revocation seals and purges the shared
owner-session cache actor. Cache writes and sealing are serialized: a write that
finishes first is removed, while a late or racing write is rejected and cannot
recreate tenant data at rest.

## Automated preflight

From the repository root, run:

```bash
.venv/bin/python -m pytest tests/test_mobile_ai_runs_api.py tests/test_mobile_api.py -q
.venv/bin/python -m pytest tests/ --ignore=tests/test_smoke.py -q -m unit --tb=short
.venv/bin/python -m pytest tests/test_smoke.py -q
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
git diff --check
```

On a supported macOS/Xcode host, generate and test the project using the commands in
`ios/README.md`. Treat generation warnings, compilation warnings introduced by this
slice, test failures, or an unexpected privacy-manifest/entitlement change as a
release blocker.

## Staging API checks

Use a non-production owner and deliberately seeded non-sensitive provenance rows.
Do not place a real credential or personal data into an error field as a test.

1. Confirm an owner receives newest-first items and a guest receives `403`.
2. Confirm a bearer token used on another tenant host receives `401`.
3. With overlapping run IDs in two tenants, confirm each host returns only its own
   provider/status/subject values.
4. Follow a cursor through multiple pages. Tampering with it or using it on another
   tenant must return `422 pagination.invalid_cursor`.
5. Send an unknown `tenant_id`/`provider` parameter and duplicate `cursor`/`limit`
   parameters. Each must return `422 request.validation_failed`; none may be ignored.
6. Revalidate page one with its ETag. Confirm an empty `304` retains
   `Cache-Control: private, no-cache`, `Vary: Authorization`, and `ETag`.
7. Inspect the exact response keys. Search the body for seeded error, correlation,
   idempotency, path, URL, raw provider, and model sentinels; none may appear.
8. Confirm missing metrics remain `null`, negative/non-finite legacy values are
   omitted, and reported cost is an integer count of micro-USD.
9. Confirm an unknown enum value maps to `other` or `unknown` and cannot break the
   page. An unrecognized provider maps to `other`; stored model text is absent.
10. Confirm an empty ledger returns a valid empty page rather than `404`.

Do not log response bodies during these checks. Request IDs, status codes, route
templates, duration, and response size are sufficient operational evidence.

## iPhone and iPad acceptance

Test a current iOS 17+ device or simulator, then repeat the authentication/offline
checks on a physical device:

- Open **AI activity** through the compact system More tab and directly from the
  iPad sidebar. Return between AI activity and Tasks and verify each navigation
  stack remains independent.
- Verify first load, pull-to-refresh, `304` revalidation, empty state, and the
  latest-500 disclosure.
- Exercise every capability option and **Needs attention** filter. Filtering is
  local and must not issue another API request.
- Verify success, disabled, provider-error, invalid-response, and unknown states use
  visible text and an icon, never color alone. Confirm every review requirement has
  understandable text.
- Verify optional latency, tokens, cost, subject, and timestamp disappear cleanly
  when absent. Confirm cost is formatted from integer micro-USD without
  floating-point arithmetic.
- Switch the device time zone, locale, 12/24-hour preference, light/dark mode, and
  iPhone/iPad orientation. Timestamps and currency formatting must remain legible.
- Test the largest Accessibility Text Size, Bold Text, Increase Contrast, Reduce
  Motion, and VoiceOver. Rows must expand vertically; VoiceOver must read the text
  status once and must not announce decorative icons.
- Load once online, terminate, relaunch offline, and verify the protected cached
  snapshot and its age appear. A failed multi-page refresh must keep the prior
  complete snapshot, not a partial mixture.
- Sign out locally, then relaunch offline; no former-tenant activity may remain.
  Repeat while a multi-page refresh and a client/project/task mutation response are
  in flight; neither response may recreate the sealed owner cache.
  For remote revocation, first bring the device online until it observes a terminal
  authentication response, then relaunch offline and verify the purge. A device that
  has not yet observed remote revocation may still show its protected cache under
  the locally restorable session. Sign into a second workspace and confirm no
  cross-tenant cache reuse.
- Confirm there is no approve, publish, apply, retry, regenerate, or start-analysis
  action anywhere on this screen.

## Performance and monitoring

Before TestFlight expansion, record first-page and five-page refresh duration,
response bytes, decoded run count, cache bytes, and scrolling responsiveness on the
oldest supported device. Investigate regressions beyond these initial targets:

- first-page server p95 under 500 ms on the intended production tenant profile;
- each 100-item JSON page under 256 KiB;
- the protected latest-500 cache under 1 MiB; and
- no sustained main-thread stall or visible scrolling hitch.

Monitor counts and rates for `200`, `304`, `401`, `403`, `422`, `429`, and contained
`500` responses, plus duration and response size. Alert on a new `500` cluster,
cross-tenant cursor anomalies, unusual response growth, or loss of `304` usage.
Never add provider/subject/error values to logs or telemetry.

## Rollout

1. Apply migrations and deploy the backend projection first.
2. Run the automated and staging API checks on at least two hosted tenants.
3. Ship to internal TestFlight and complete the iPhone/iPad acceptance list.
4. Review App Store privacy answers and screenshots against the actual read-only
   behavior; do not describe this screen as approving or generating content.
5. Expand TestFlight gradually while monitoring auth, validation, latency, response
   size, and crash-free sessions.

The endpoint is read-only and does not require a data backfill. Existing valid
ledger rows appear immediately; a studio with none receives the native empty state.

## Incident response and rollback

For suspected tenant crossover, sensitive-field exposure, unsafe cache retention,
or malformed-response crash:

1. Stop TestFlight expansion and preserve request IDs, route/status aggregates,
   affected app/backend versions, and tenant identifiers. Do not copy response
   bodies or raw ledger values into tickets or chat.
2. Roll the mobile API deployment back to the last version without the route, or
   deploy a fixed projection. The ledger is append-only and this route performs no
   business-state mutation, so no data rollback is required.
3. Revoke affected owner sessions (or rotate owner credentials when broader
   invalidation is required). On the next authenticated request, terminal auth
   handling purges the protected device cache.
4. If a shipped app itself retained an unsafe DTO, halt that build and issue a fixed
   build that removes the field and purges the old cache key before re-enabling the
   screen.
5. Re-run two-tenant isolation, exact-key redaction, ETag, offline purge, and
   physical-device checks before resuming rollout.

Do not add a raw diagnostic field as an incident workaround. Operators can inspect
the existing server-side admin ledger through its separate authenticated boundary.

## Deferred work

This AI activity screen remains read-only. Milestone 5B.3's separately gated native
caption suggestions use their own immutable, session-bound, no-callback operation
contract and cannot be started or applied from this ledger. Native Analyze actions,
production telemetry, and automatic action from model output remain out of scope.
In hosted mode, no callback-based native Analyze mutation may ship until callbacks
carry explicit authenticated tenant authority instead of relying on a tenant-local
numeric gallery ID and a global callback origin.
