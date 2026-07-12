# ADR 0057 — Hosted backups + CI actually running the hosted suite

**Status:** Accepted; amended 2026-07-11 for transient Content data
**Date:** 2026-07-02
**Deciders:** Kevin (owner), principal engineer

## Context

Two findings from the launch-readiness audit, one of them business-ending:

1. **Zero hosted backups.** Nothing snapshotted `SAAS_TENANT_DATA_DIR` (every tenant's
   SQLite DB + media) or the control DB (billing state, password hashes, per-tenant
   Stripe keys). The legacy `ops/backup.sh` hardcodes the bare-metal single-tenant path
   and its systemd timer doesn't exist in the container; the in-app "backup stale" alarm
   pointed at that nonexistent unit. A disk failure or a bad `docker volume rm` was
   unrecoverable, silent, total data loss for every customer at once.
2. **CI never ran the hosted test suite.** The CI unit step selects `-m unit`, and
   `tests/test_saas.py`, `test_saas_hosted_smoke.py`, and `test_onboarding.py` carried no
   marker — every hosted-product test added since the transformation began (isolation,
   billing, payments, identity — ~120 tests) was silently **deselected** in CI. Green
   checkmarks were only asserting the legacy suite.

## Decision

**1. A backup sidecar in the compose stack.** `scripts/hosted-backup.py --loop` (compose
`backup` service, reusing the app image; `rclone` added to the image) runs every
`MISE_BACKUP_INTERVAL_HOURS` (default 24):

- **Consistent, sanitized DB snapshots** — every live tenant DB + the control DB via
  SQLite's backup API (correct under WAL). The exact control snapshot supplies the live,
  parked, purged, and remote-cleanup inventory. Tenant destinations revoke native API
  sessions/tokens, disable/clear push, fail pending native push/content jobs, finish active
  usage, and scrub transient suggestion input/output before `VACUUM`, schema,
  `PRAGMA quick_check`, foreign-key, and gzip validation. Live DBs land under `tenants/`;
  parked `.trash` DBs land under `trash/`.
- **Publish one whole manifest generation** — all work is built under a same-filesystem
  `.generation-<stamp>/`; per-file `.mise-staging-*` and partial gzip files are never
  publishable. `manifest.json` records the stamp, control archive, expected live/parked
  identities, captured counts, and failures. The whole directory atomically renames to
  `/data/backups/<stamp>/`. `complete=true` is structural; a clean restore additionally
  requires `failures=[]` and matching expected/captured counts.
- **One pass at a time** — the hosted sidecar/manual entrypoint acquires a non-blocking
  OS `flock` on `.hosted-backup.lock` for snapshot, prune, marker, and off-site sync.
  An overlapping invocation fails loudly. The single-tenant shell backup independently
  uses `.backup.lock`; lock contention is never treated as fresh-backup evidence.
- **Off-site is encrypted, least privilege, and manifest-committed** — local generations
  share the volume they protect. The backup service alone receives the read-only regular
  file at `MISE_RCLONE_CONFIG_PATH`; it must be a least-privilege rclone `crypt` config,
  host UID/GID 10001, mode 0400, with its crypt password/salt separately escrowed and
  recovery-tested off-host. `MISE_BACKUP_RCLONE_REMOTE_ENCRYPTED=true` is an explicit
  operator acknowledgement, not encryption by itself.
- **Remote order is media, payload, manifest** — rclone first syncs only exact
  control-derived `media`, `brand`, and `receipts` roots, denying live DB companions,
  tmp/export ZIPs, and orphans; displaced media moves to
  `tenants-history/<stamp>/`. It then copies only the current generation payload while
  excluding `manifest.json`, and finally `copyto`s that manifest as the remote commit
  record. A directory without its manifest is incomplete. Old DB generations and media
  history are immutable/operator-owned lifecycle, not automatically pruned by Mise.
- **Retention** prunes local snapshot dirs past `MISE_BACKUP_RETENTION_DAYS` (default 14).
- **Evidence has separate meanings** — the local heartbeat publishes last. Tenant and
  off-site failure markers stay durable on partial/failing passes; an off-site success
  marker contains the exact manifest-committed generation stamp. Runtime preflight requires
  a safe generation name, fresh success evidence, no failure marker, `complete=true`,
  `failures=[]`, matching expected/captured counts, and every listed regular
  control/live/parked payload before ingress. The legacy single-tenant check is untouched.
- **A written restore drill** (runbook §10) covering single-tenant restore and
  full-disk-loss recovery. Recovery selects one manifest generation, verifies failures and
  counts, restores same-generation control/live/parked DBs through quarantine, admits only
  control-derived durable media, reconstructs retired-path guards, and reconciles Stripe.
  Native sessions/tokens stay revoked, push stays disabled until re-registration, and
  active suggestion operations restore content-free and never resume.

**2. The hosted suite joins the CI unit gate.** The three modules are marked
`pytest.mark.unit` (they are fast and hermetic — tmp-path DBs, no network), roughly
doubling what CI actually verifies on every push.

**3. `.env.example` documents the operational surface** — including the Caddy site
address, support/alerting settings, encrypted-remote acknowledgement, and the absolute
backup-only `MISE_RCLONE_CONFIG_PATH`. The config/key contents remain outside `.env`.

## Consequences

- **Customer data survives the failure modes that matter**: corruption/mistakes (local
  snapshots, 14 days), disk loss (off-site sync), and *silently-stopped backups* (the
  marker + Telegram alarm).
- **Short-lived provider material does not become ordinary restore material.** Destination
  scrub + `VACUUM` limits snapshot remanence, while live DB/WAL/SHM exclusions keep
  unsanitized runtime files out of the media mirror. Normal live cleanup remains logical
  plus `secure_delete`/best-effort checkpoint; it is not an instantaneous forensic-erasure
  guarantee when readers pin WAL or historical remote objects still exist.
- **DB and media recovery is intentionally not called atomic.** Each SQLite file is a
  consistent point-in-time snapshot; media sync occurs afterward. Current media plus
  per-sync history needs reconciliation for files changed near the backup boundary.
- **CI green now means the hosted product passed**, not just the legacy studio.
- Ops surface is discoverable from `.env.example` instead of from reading `config.py`.
- The amendment crosses auth-adjacent and recovery boundaries: backup copies scrub
  sessions, push tokens, and transient provider context before they can be restored.
  Treat future changes to this path as security-sensitive and rehearse restore behavior.

## Alternatives considered

- **restic instead of rclone.** Deferred — restic adds encrypted, deduplicated versioned
  archives, but needs repo init/key management. Rclone still requires a crypt-wrapped
  least-privilege backend, protected config mount, off-host key escrow, lifecycle, and
  restore drills; it is not “one env var.” Revisit post-beta.
- **Backing up media locally too.** Rejected — a same-disk media copy doubles disk usage
  and survives nothing the DB snapshots don't; media protection belongs off-site.
- **Cron on the host instead of a sidecar.** Rejected — host cron is outside the
  compose stack (invisible to `docker compose ps`, lost on host rebuild); the sidecar
  ships with the deploy and restarts with it.

## Migration note for existing remotes

The allowlist/exclusions govern future syncs; they do not prove old raw databases,
WAL/SHM/journal companions, plaintext/partial generations, old export or generated ZIPs,
or pre-amendment archives vanished from remote current/history. Native Content stays off
until an operator creates and restore-checks a clean manifest generation, inventories every
remote/history tier, obtains human approval for destructive cleanup or a bounded lifecycle,
and verifies prohibited objects are gone. This one-time purge is a human release gate, not
an automatic backup-sidecar action. Local retention or local deleted-studio purge never
claims remote deletion.
