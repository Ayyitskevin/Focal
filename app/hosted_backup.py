"""Hosted backup — consistent SQLite snapshots for every tenant + the control DB.

Two layers, honestly separated (ADR 0057):

- **Local snapshots** (``<data>/backups/<stamp>/``) protect against corruption and
  operator mistakes: every tenant DB and the control DB are copied via SQLite's
  backup API (point-in-time consistent under WAL), integrity-checked, then gzipped.
  Old snapshot directories are pruned by retention.
- **Off-site sync** (optional, ``MISE_BACKUP_RCLONE_REMOTE``) is what survives disk
  loss: rclone syncs the snapshot dir *and* the tenant media tree to the remote.
  Local-only backups live on the same volume as the data they protect — the module
  reports that state loudly rather than pretending otherwise.

A marker file (``backups/.last-hosted-backup``) lets ops_monitor assert the
positive — "newest backup is N hours old" — instead of inferring health from
silence. Run via ``scripts/hosted-backup.py`` (compose ``backup`` sidecar).
"""

from __future__ import annotations

import gzip
import logging
import shutil
import sqlite3
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("mise.hosted_backup")

MARKER_NAME = ".last-hosted-backup"
# Companion signal: written (newline-joined tenant slugs) when a pass backs up
# some studios but a per-tenant snapshot FAILED, cleared on a fully-clean pass.
# The heartbeat marker alone can't express partial failure — a single tenant
# whose DB is corrupt would otherwise accrue zero backups while the marker stays
# fresh and ops_monitor reads only its mtime (ADR 0057: assert the positive, and
# a partial success is not a full one).
FAILURE_MARKER_NAME = ".last-hosted-backup-failures"


def _snapshot_sqlite(src: Path, dest_gz: Path) -> None:
    """Consistent, integrity-checked, gzipped copy of one SQLite database."""
    dest_plain = dest_gz.with_suffix("")  # strip .gz
    src_con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        dst_con = sqlite3.connect(dest_plain)
        try:
            src_con.backup(dst_con)
            ok = dst_con.execute("PRAGMA quick_check").fetchone()[0]
            if ok != "ok":
                raise RuntimeError(f"integrity check failed for {src}: {ok}")
        finally:
            dst_con.close()
    finally:
        src_con.close()
    with open(dest_plain, "rb") as f_in, gzip.open(dest_gz, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    dest_plain.unlink()


def _prune(backups_dir: Path, retention_days: int) -> int:
    cutoff = time.time() - retention_days * 86400
    pruned = 0
    for entry in backups_dir.iterdir():
        if entry.is_dir() and entry.stat().st_mtime < cutoff:
            shutil.rmtree(entry, ignore_errors=True)
            pruned += 1
    return pruned


def _offsite_sync(remote: str, backups_dir: Path, tenants_dir: Path) -> str:
    """rclone sync of snapshots + tenant media to the remote; returns a status word."""
    if not remote:
        return "off"
    if shutil.which("rclone") is None:
        log.error("MISE_BACKUP_RCLONE_REMOTE is set but rclone is not installed")
        return "failed:rclone-missing"
    for src, sub in ((backups_dir, "backups"), (tenants_dir, "tenants")):
        try:
            subprocess.run(
                ["rclone", "sync", str(src), f"{remote.rstrip('/')}/{sub}"],
                check=True,
                capture_output=True,
                timeout=6 * 3600,
            )
        except Exception as exc:
            log.error("offsite sync of %s failed: %s", sub, exc)
            return f"failed:{sub}"
    return "synced"


def run_backup(
    data_dir: Path,
    tenants_dir: Path,
    control_db: Path,
    *,
    retention_days: int = 14,
    rclone_remote: str = "",
) -> dict:
    """One backup pass. Raises only if NOTHING could be backed up."""
    backups_dir = data_dir / "backups"
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    dest = backups_dir / stamp
    dest.mkdir(parents=True, exist_ok=True)

    control_done = False
    if control_db.exists():
        _snapshot_sqlite(control_db, dest / "saas-control.db.gz")
        control_done = True

    tenant_count = 0
    failed_tenants: list[str] = []
    tenant_dest = dest / "tenants"
    if tenants_dir.exists():
        for entry in sorted(tenants_dir.iterdir()):
            # .trash holds deleted-studio parking (ADR 0051) — media is preserved
            # by the offsite sync of tenants_dir; DB snapshots cover live studios.
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            db_file = entry / "mise.db"
            if not db_file.exists():
                continue
            try:
                tenant_dest.mkdir(exist_ok=True)
                _snapshot_sqlite(db_file, tenant_dest / f"{entry.name}.db.gz")
                tenant_count += 1
            except Exception:
                # One broken tenant DB must not stop the other studios' backups.
                log.exception("backup failed for tenant %s", entry.name)
                failed_tenants.append(entry.name)

    if not control_done and tenant_count == 0:
        shutil.rmtree(dest, ignore_errors=True)
        raise RuntimeError(f"nothing to back up under {data_dir} — wrong paths?")

    (backups_dir / MARKER_NAME).write_text(datetime.now(UTC).isoformat())
    # Surface partial failure to ops_monitor via a durable file signal (the sidecar
    # runs in its own process, so a return value can't reach the app's monitor). A
    # fully-clean pass clears any stale failure marker so the alert self-resolves.
    failure_marker = backups_dir / FAILURE_MARKER_NAME
    if failed_tenants:
        failure_marker.write_text("\n".join(failed_tenants))
    else:
        failure_marker.unlink(missing_ok=True)
    pruned = _prune(backups_dir, retention_days)
    offsite = _offsite_sync(rclone_remote, backups_dir, tenants_dir)
    summary = {
        "snapshot": str(dest),
        "control": control_done,
        "tenants": tenant_count,
        "tenant_failures": len(failed_tenants),
        "failed": failed_tenants,
        "pruned": pruned,
        "offsite": offsite,
    }
    log.info("hosted backup complete: %s", summary)
    return summary
