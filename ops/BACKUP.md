# Mise backup & restore — deployed topology

The **deployed** model is a **mickey-pull**, not a flow-push. Three scheduled stages,
two machines, each fails loud:

| When  | Where  | Mechanism | What |
|-------|--------|-----------|------|
| 02:30 | flow   | `mise-backup.timer` → `mise-backup.service` (systemd, **enabled**) → `ops/backup.sh` | Consistent WAL-safe `sqlite3 .backup` of `/opt/mise/data/mise.db`, **integrity-checked before it is kept**, gzipped to `/opt/mise/data/backups/`, 14-day local retention. Local only — shares a disk with the live data. |
| 03:30 | mickey | cron → `~/.local/bin/mise-backup-pull` | rsync flow's `backups/` (DB snapshots), `media/`, `brand/` → `~/backups/mise/{db,media,brand}/`. DB mirrors flow's pruned set; media/brand are archive-only (never `--delete`). This is the real off-disk durability. |
| 04:00 | mickey | cron → `~/.local/bin/mise-backup-verify` | **The restore gate.** Gunzips the newest `~/backups/mise/db/*.db.gz` to a throwaway file and proves it restores: `PRAGMA integrity_check` = ok, `PRAGMA foreign_key_check` empty, core tables (`schema_migrations`/`galleries`/`clients`/`invoices`) present + queryable, `schema_migrations` non-empty. Also checks **freshness** (newest snapshot < 26h — a stale newest means the pull or flow's snapshot silently stalled). Any failure → one Telegram alert via `~/.config/mise-uptime.env` (same channel as `mise-uptime-check`; one-shot `sendMessage`, never `getUpdates`, so it can't clash with MickeyBot). Clean runs are quiet. Logs to `~/.local/state/mise-backup-verify.log`; failures also append to `shared/logs/mickey-actions.log`. Run `mise-backup-verify --test` to fire a test alert.

A backup is not "done" until a restore is verified — stage 3 is what makes the off-site
copy trustworthy instead of merely present.

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

## Not deployed (original flow-push design — superseded)

These files are the earlier push-model design and are **not installed** anywhere
(`systemctl list-unit-files mise-offsite*` on flow returns nothing). Kept for reference;
the mickey-pull above replaced them. Candidates for pruning:

- `ops/mise-offsite.service`, `ops/mise-offsite.timer` — flow-side push timer.
- `ops/offsite-sync.sh` — flow pushes snapshot+media to mickey.
- `ops/restore-test.sh` — flow-side restore gate (depends on flow's live media tree;
  `mise-backup-verify` is the deployed, mickey-local equivalent).

## Restore (manual)

```sh
# newest off-site snapshot on mickey
gunzip -k ~/backups/mise/db/mise-YYYY-MM-DD-HHMM.db.gz
sqlite3 ~/backups/mise/db/mise-YYYY-MM-DD-HHMM.db "PRAGMA integrity_check;"
# then copy into place on flow as the mise user, stop mise, swap /opt/mise/data/mise.db, start mise
```
