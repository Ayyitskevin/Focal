# Mise backup & restore — deployed topology

This page owns the **single-tenant flow → mickey** deployment. Hosted Compose uses
manifest-backed generations, a backup-only rclone crypt mount, and separate live/parked
inventory; hosted operators must use runbook §10, never the newest-file procedure below.

The **deployed** model is a **mickey-pull**, not a flow-push. Three scheduled stages,
two machines, each fails loud:

| When  | Where  | Mechanism | What |
|-------|--------|-----------|------|
| 02:30 | flow   | `mise-backup.timer` → `mise-backup.service` (systemd, **enabled**) → `ops/backup.sh` | Consistent WAL-safe `sqlite3 .backup` of `${MISE_DATA_DIR:-/opt/mise/data}/mise.db`; the destination revokes native sessions/tokens, disables push, fails queued native work, finishes active usage, and scrubs transient suggestions before `VACUUM`, integrity/schema/foreign-key checks, and gzip. Raw/gzip work stays under the same-volume `.backup-staging/`; only the complete archive moves into `backups/`. Fourteen-day local retention. Local only — shares a disk with the live data. |
| 03:30 | mickey | cron → `~/.local/bin/mise-backup-pull` | rsync flow's `backups/` (DB snapshots), `media/`, `brand/` → `~/backups/mise/{db,media,brand}/`. DB mirrors flow's pruned set; media/brand are archive-only (never `--delete`). This is the real off-disk durability. |
| 04:00 | mickey | cron → `~/.local/bin/mise-backup-verify` | **The restore gate.** Gunzips the newest `~/backups/mise/db/*.db.gz` to a throwaway file and proves it restores: `PRAGMA integrity_check` = ok, `PRAGMA foreign_key_check` empty, core tables (`schema_migrations`/`galleries`/`clients`/`invoices`) present + queryable, `schema_migrations` non-empty. Also checks **freshness** (newest snapshot < 26h — a stale newest means the pull or flow's snapshot silently stalled). Any failure → one Telegram alert via `~/.config/mise-uptime.env` (same channel as `mise-uptime-check`; one-shot `sendMessage`, never `getUpdates`, so it can't clash with MickeyBot). Clean runs are quiet. Logs to `~/.local/state/mise-backup-verify.log`; failures also append to `shared/logs/mickey-actions.log`. Run `mise-backup-verify --test` to fire a test alert.

A backup is not "done" until a restore is verified — stage 3 is what makes the off-site
copy trustworthy instead of merely present.

## Native caption privacy boundary

The live database is the source of truth and is never scrubbed by the backup job. The job
scrubs only its destination copy: queued/running/ready/failed native suggestion rows are
detached, cleared of context/candidate/provider/model, and normalized to a content-free
`failed/session_ended` result. Active usage claims finish; native API sessions/tokens are
revoked; push devices/tokens are disabled/cleared; and pending native deliveries/content
jobs fail. It commits and `VACUUM`s before compression so deleted text does not remain in
free pages of the archive. A restored owner must log in, register for push, and make any
new provider request explicitly.

Live connections use SQLite `secure_delete`, and normal TTL/logout cleanup attempts a
truncate checkpoint. That is logical erasure with best-effort physical cleanup, not an
instant forensic guarantee: a reader may pin old WAL frames, and OS/filesystem snapshots or
older backup objects keep their own lifecycle. A repeated `mobile caption cleanup WAL
checkpoint remains busy` warning needs investigation and retry.

The staging directory is under `MISE_DATA_DIR`, outside `backups/`, so the final `mv` is on
the same filesystem and the mickey pull never sees raw/plain/partial work. The next pass
deletes abandoned staging files before building another snapshot. A failed scrub,
integrity check, or gzip leaves no new archive in `backups/`.

`ops/backup.sh` holds a non-blocking OS lock on `$MISE_DATA_DIR/.backup.lock` for the whole
pass. An overlapping timer/manual invocation exits 75 with `BACKUP SKIPPED`; it does not
snapshot, prune, or publish and must not advance freshness monitoring. Investigate a
repeated lock skip rather than treating it as successful redundancy.

### One-time gate before enabling native suggestions

Older backup versions did not perform this scrub. Before enablement:

1. Run one new backup and let the mickey restore gate verify it.
2. Inventory `~/backups/mise/db/` and every configured remote/history tier for raw
   `mise.db`, `mise.db-wal`, `mise.db-shm`, plaintext/partial staging files, and archives
   created before this behavior landed. Do not print or inspect caption payloads.
3. With explicit human approval, purge those objects or apply a documented expiration
   lifecycle. Preserve only backups the owner has deliberately accepted under the new
   retention policy; a backup does not make remote deletion non-destructive.
4. Re-list the stores, record the cutoff, and perform another restore check. New filters and
   sanitized archives do not prove historical remote copies disappeared.

## Why mickey-local (not the flow-push units below)

The off-site copy lives on mickey (the always-on node). Verifying it **in place on
mickey** means the check survives flow being down — the exact scenario a backup exists
for. It also reuses the cron + `mise-uptime.env` pattern already proven by
`mise-backup-pull` and `mise-uptime-check`, instead of a flow-side systemd unit that
dies with the box it protects.

`mise-backup-pull` and `mise-backup-verify` are machine-local on mickey
(`~/.local/bin/`, not version-controlled here, like their `mise-uptime-check` sibling);
their contents are captured in ORACLE. This file is the repo-side source of truth for
the **topology**.

## History

An earlier **flow-push** design (`mise-offsite.service`/`.timer`, `offsite-sync.sh`,
`restore-test.sh`) was never installed and was **pruned 2026-06-25** in favour of the
mickey-pull above — verifying the off-site copy on mickey survives flow being down, which
is the whole point of a backup. `ops/` now holds only the deployed `backup.sh` +
`mise-backup.service`/`.timer`; the pull/verify scripts are machine-local on mickey.

## Restore (manual)

```sh
# choose an archive whose 04:00 restore gate recorded integrity/schema/FK success;
# do not select solely because its filename is newest
gunzip -k ~/backups/mise/db/mise-YYYY-MM-DD-HHMMSS-NNNNNNNNN.db.gz
sqlite3 ~/backups/mise/db/mise-YYYY-MM-DD-HHMMSS-NNNNNNNNN.db "PRAGMA integrity_check;"
# then copy into place on flow as the mise user; stop mise before swapping
# /opt/mise/data/mise.db or removing its stale -wal/-shm companions, then start mise
# and delete the temporary plaintext restore copy after verification
```

After startup, verify `/healthz`, the expected tenant/studio identity, core-table counts, and
that native API sessions/tokens are revoked, push devices/tokens remain disabled/cleared,
pending native deliveries/jobs remain failed, and in-flight suggestions are content-free
`failed/session_ended`. The separately pulled media/brand trees are not atomic with the DB
snapshot; reconcile file presence against DB records rather than claiming a point-in-time
image. Sanitization does not revoke signed browser/portal cookies, so rotate
`MISE_SECRET_KEY` and affected passwords when compromise is part of the restore reason.
