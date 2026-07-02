# ADR 0057 — Hosted backups + CI actually running the hosted suite

**Status:** Accepted (launch Phase 2, slice 1 — the survivable-host work begins)
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

- **Consistent DB snapshots** — every tenant DB + the control DB via SQLite's backup API
  (correct under WAL), `PRAGMA quick_check`-verified before being kept, gzipped into
  `/data/backups/<stamp>/`. One broken tenant DB is counted and logged but never stops
  the other studios' backups. `.trash` (deleted-studio parking) is not snapshotted as a
  live tenant; its files ride the media sync.
- **Off-site is the disk-loss answer, and it says so** — local snapshots share the volume
  with the data they protect. `MISE_BACKUP_RCLONE_REMOTE` syncs the snapshot dir *and*
  the whole tenant media tree off-site each pass; unset reports `off`, broken reports
  `failed:*` and exits non-zero. Never a silent no-op.
- **Retention** prunes local snapshot dirs past `MISE_BACKUP_RETENTION_DAYS` (default 14).
- **A heartbeat marker** (`backups/.last-hosted-backup`) is stamped after each pass;
  `ops_monitor`'s hosted branch alerts when it's missing or stale — asserting the
  positive instead of inferring health from silence. The legacy single-tenant check is
  untouched.
- **A written restore drill** (runbook §10) covering single-tenant restore and
  full-disk-loss recovery, including the rule that live DB files in the media sync are
  not crash-consistent — DBs restore from snapshots.

**2. The hosted suite joins the CI unit gate.** The three modules are marked
`pytest.mark.unit` (they are fast and hermetic — tmp-path DBs, no network), roughly
doubling what CI actually verifies on every push.

**3. `.env.example` now documents the operational surface** — the Caddy site address
(required by the deploy), support email, dunning-grace and signup-throttle knobs, and
the Telegram alerting vars, with an explicit warning that a by-the-book deploy without
Telegram has **no alerting at all**; plus the new backup knobs.

## Consequences

- **Customer data survives the failure modes that matter**: corruption/mistakes (local
  snapshots, 14 days), disk loss (off-site sync), and *silently-stopped backups* (the
  marker + Telegram alarm).
- **CI green now means the hosted product passed**, not just the legacy studio.
- Ops surface is discoverable from `.env.example` instead of from reading `config.py`.
- Green-light change: no money path, no auth, no schema; the app container is untouched
  except for the `rclone` package.

## Alternatives considered

- **restic instead of rclone.** Deferred — restic adds encrypted, deduplicated versioned
  archives (better), but needs repo init/key management; rclone to an object store is
  one env var and the snapshots are already versioned by directory. Revisit post-beta.
- **Backing up media locally too.** Rejected — a same-disk media copy doubles disk usage
  and survives nothing the DB snapshots don't; media protection belongs off-site.
- **Cron on the host instead of a sidecar.** Rejected — host cron is outside the
  compose stack (invisible to `docker compose ps`, lost on host rebuild); the sidecar
  ships with the deploy and restarts with it.
