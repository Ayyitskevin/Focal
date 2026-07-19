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
import re
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
_TENANT_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,30}[a-z0-9])$")


def _snapshot_sqlite(
    src: Path,
    dest_gz: Path,
    *,
    require_tenant_identity: bool = False,
    read_tenant_roster: bool = False,
) -> set[str] | None:
    """Consistent, integrity-checked, gzipped copy of one SQLite database."""
    dest_plain = dest_gz.with_suffix("")  # strip .gz
    roster: set[str] | None = None
    try:
        src_con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        try:
            if require_tenant_identity:
                marker_table = src_con.execute(
                    "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='schema_migrations'"
                ).fetchone()
                marker = (
                    src_con.execute(
                        "SELECT 1 FROM schema_migrations WHERE name='001_init.sql'"
                    ).fetchone()
                    if marker_table
                    else None
                )
                if marker is None:
                    raise RuntimeError(f"tenant database identity missing for {src}")
            dst_con = sqlite3.connect(dest_plain)
            try:
                src_con.backup(dst_con)
                ok = dst_con.execute("PRAGMA quick_check").fetchone()[0]
                if ok != "ok":
                    raise RuntimeError(f"integrity check failed for {src}: {ok}")
                if read_tenant_roster:
                    # Read from the archived image, not the live control DB: the
                    # roster and control snapshot must describe the same instant.
                    roster = _tenant_roster(dst_con)
            finally:
                dst_con.close()
        finally:
            src_con.close()
        with open(dest_plain, "rb") as f_in, gzip.open(dest_gz, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        dest_plain.unlink()
        return roster
    except BaseException:
        # Never leave an unverified plain DB or partial gzip in a snapshot tree.
        dest_plain.unlink(missing_ok=True)
        dest_gz.unlink(missing_ok=True)
        raise


def _tenant_roster(con: sqlite3.Connection) -> set[str]:
    """Return validated retained slugs from one archived control-DB image."""

    try:
        rows = con.execute("SELECT slug FROM tenants WHERE deleted_at IS NULL").fetchall()
    except sqlite3.Error as exc:
        raise RuntimeError("hosted control snapshot has no readable tenant roster") from exc
    live: set[str] = set()
    for (slug,) in rows:
        if not isinstance(slug, str) or not _TENANT_SLUG_RE.fullmatch(slug):
            raise RuntimeError("hosted control database contains an invalid tenant slug")
        live.add(slug)
    return live


def _prune(backups_dir: Path, retention_days: int) -> int:
    cutoff = time.time() - retention_days * 86400
    pruned = 0
    for entry in backups_dir.iterdir():
        if entry.is_dir() and entry.stat().st_mtime < cutoff:
            shutil.rmtree(entry, ignore_errors=True)
            pruned += 1
    return pruned


def _offsite_sync(remote: str, backups_dir: Path, tenants_dir: Path, stamp: str) -> str:
    """rclone sync of snapshots + tenant media to the remote; returns a status word.

    Uses ``--backup-dir`` so this is a VERSIONED mirror, not a bare one: files the
    sync would otherwise delete or overwrite on the remote (because they vanished or
    changed locally) are moved into ``<remote>/<sub>-history/<stamp>/`` instead of
    being destroyed. Without it, local corruption or an accidental deletion is
    propagated to the off-site copy on the very next pass — the exact disaster the
    off-site layer exists to survive. The operator prunes ``*-history/`` per their
    retention policy (rclone keeps no automatic bound).
    """
    if not remote:
        return "off"
    if shutil.which("rclone") is None:
        log.error("MISE_BACKUP_RCLONE_REMOTE is set but rclone is not installed")
        return "failed:rclone-missing"
    base = remote.rstrip("/")
    for src, sub in ((backups_dir, "backups"), (tenants_dir, "tenants")):
        try:
            subprocess.run(
                [
                    "rclone",
                    "sync",
                    str(src),
                    f"{base}/{sub}",
                    "--backup-dir",
                    f"{base}/{sub}-history/{stamp}",
                ],
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
    """One backup pass anchored to one authoritative control-DB snapshot."""
    backups_dir = data_dir / "backups"
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    dest = backups_dir / stamp
    dest.mkdir(parents=True, exist_ok=True)

    if not control_db.is_file():
        shutil.rmtree(dest, ignore_errors=True)
        raise RuntimeError(f"nothing to back up under {data_dir} — control database missing")
    try:
        expected_tenants = _snapshot_sqlite(
            control_db,
            dest / "saas-control.db.gz",
            read_tenant_roster=True,
        )
        if expected_tenants is None:
            raise RuntimeError("hosted control snapshot did not produce a tenant roster")
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise

    tenant_count = 0
    failed_tenants: list[str] = []
    tenant_dest = dest / "tenants"
    # The control roster is authoritative. Tombstones and unregistered directories
    # are not live tenants; .trash and media are covered by the off-site tree sync.
    candidate_tenants = sorted(expected_tenants)
    for slug in candidate_tenants:
        db_file = tenants_dir / slug / "mise.db"
        if not db_file.is_file():
            log.error("backup missing for retained tenant %s", slug)
            failed_tenants.append(slug)
            continue
        try:
            tenant_dest.mkdir(exist_ok=True)
            _snapshot_sqlite(
                db_file,
                tenant_dest / f"{slug}.db.gz",
                require_tenant_identity=True,
            )
            tenant_count += 1
        except Exception:
            # One broken tenant DB must not stop the other studios' backups.
            log.exception("backup failed for tenant %s", slug)
            failed_tenants.append(slug)

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
    offsite = _offsite_sync(rclone_remote, backups_dir, tenants_dir, stamp)
    summary = {
        "snapshot": str(dest),
        "control": True,
        "tenants": tenant_count,
        "tenant_failures": len(failed_tenants),
        "failed": failed_tenants,
        "pruned": pruned,
        "offsite": offsite,
    }
    log.info("hosted backup complete: %s", summary)
    return summary
