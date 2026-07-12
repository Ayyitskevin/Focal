"""Hosted backup — consistent SQLite snapshots for every tenant + the control DB.

Two layers, honestly separated (ADR 0057):

- **Local snapshots** (``<data>/backups/<stamp>/``) protect against corruption and
  operator mistakes: every tenant DB and the control DB are copied via SQLite's
  backup API (point-in-time consistent under WAL), integrity-checked, then gzipped.
  Old snapshot directories are pruned by retention.
- **Off-site sync** (optional, ``MISE_BACKUP_RCLONE_REMOTE``) is what survives disk
  loss: rclone versions allowlisted durable media, copies only the current
  generation payload, then publishes its manifest as the remote commit record.
  Local-only backups live on the same volume as the data they protect — the module
  reports that state loudly rather than pretending otherwise.

A marker file (``backups/.last-hosted-backup``) lets ops_monitor assert the
positive — "newest backup is N hours old" — instead of inferring health from
silence. Run via ``scripts/hosted-backup.py`` (compose ``backup`` sidecar).
"""

from __future__ import annotations

import fcntl
import gzip
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from contextlib import contextmanager
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
OFFSITE_FAILURE_MARKER_NAME = ".last-hosted-backup-offsite-failure"
OFFSITE_SUCCESS_MARKER_NAME = ".last-hosted-backup-offsite-success"
LOCK_NAME = ".hosted-backup.lock"
MANIFEST_NAME = "manifest.json"
_GENERATION_RE = re.compile(r"^\d{8}-\d{6}-\d{6}$")
_PRUNABLE_GENERATION_RE = re.compile(r"^\d{8}-\d{6}(?:-\d{6})?$")


def _fsync_directory(path: Path) -> None:
    directory_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _atomic_write_text(path: Path, value: str) -> None:
    staged: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
            staged = Path(handle.name)
        staged.replace(path)
        _fsync_directory(path.parent)
    except BaseException:
        if staged is not None:
            staged.unlink(missing_ok=True)
        raise


def _durable_unlink(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    path.unlink()
    _fsync_directory(path.parent)


@contextmanager
def _exclusive_backup_lock(data_dir: Path):
    """Reject overlapping sidecar/manual passes with an OS-released file lock."""

    data_dir.mkdir(parents=True, exist_ok=True)
    lock_path = data_dir / LOCK_NAME
    with lock_path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("a hosted backup pass is already running") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _scrub_transient_mobile_content(con: sqlite3.Connection) -> None:
    """Remove short-lived provider input/output from a destination snapshot.

    The live database remains untouched. VACUUM after this update rebuilds the
    snapshot so prior text cannot survive in free pages inside the compressed DB.
    """

    tables = {
        str(row[0]) for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    suggestion_table = con.execute(
        """SELECT 1 FROM sqlite_master
              WHERE type='table' AND name='mobile_caption_suggestions'"""
    ).fetchone()
    usage_table = con.execute(
        """SELECT 1 FROM sqlite_master
              WHERE type='table' AND name='mobile_caption_usage'"""
    ).fetchone()
    session_tables = {"api_sessions", "api_tokens"} <= tables
    push_tables = {"mobile_push_devices", "mobile_notification_deliveries"} <= tables
    if (
        suggestion_table is None
        and usage_table is None
        and not session_tables
        and not push_tables
        and "jobs" not in tables
    ):
        return
    if session_tables:
        now_epoch = int(time.time())
        con.execute(
            """UPDATE api_sessions
                  SET revoked_at=COALESCE(revoked_at,?),
                      revoke_reason=COALESCE(revoke_reason,'backup_restore')
                WHERE revoked_at IS NULL""",
            (now_epoch,),
        )
        con.execute(
            """UPDATE api_tokens SET revoked_at=COALESCE(revoked_at,?)
                WHERE revoked_at IS NULL""",
            (now_epoch,),
        )
    if push_tables:
        con.execute(
            """UPDATE mobile_push_devices
                  SET active=0,session_id=NULL,token_ciphertext=NULL,
                      disabled_reason='backup_restore',
                      disabled_at=COALESCE(disabled_at,datetime('now')),
                      updated_at=datetime('now')"""
        )
        con.execute(
            """UPDATE mobile_notification_deliveries
                  SET status='failed',claim_token=NULL,claimed_at=NULL,
                      queued_job_id=NULL,reason='backup_restore',
                      updated_at=datetime('now')
                WHERE status IN ('queued','sending','retry')"""
        )
    if "jobs" in tables:
        con.execute(
            """UPDATE jobs
                  SET status='failed',error='backup_restore',updated_at=datetime('now')
                WHERE status IN ('queued','running')
                  AND kind IN ('apns_delivery','mobile_caption_suggestion')"""
        )
    if usage_table is not None:
        # Restores must not inherit concurrency capacity from work that cannot
        # resume. Keep accepted_at as non-content daily quota evidence.
        con.execute(
            """UPDATE mobile_caption_usage
                  SET state='finished',finished_at=COALESCE(finished_at,datetime('now'))
                WHERE state='active'"""
        )
    if suggestion_table is not None:
        con.execute(
            """UPDATE mobile_caption_suggestions
                  SET session_id=NULL,
                      status=CASE
                          WHEN status IN ('queued','running','ready','failed')
                          THEN 'failed'
                          ELSE status
                      END,
                      context_json=NULL,
                      candidate_text=NULL,
                      provider=NULL,
                      model=NULL,
                      failure_code=CASE
                          WHEN status IN ('queued','running','ready','failed')
                          THEN 'session_ended'
                          ELSE NULL
                      END,
                      completed_at=CASE
                          WHEN status IN ('queued','running','ready','failed')
                          THEN COALESCE(completed_at, datetime('now'))
                          ELSE completed_at
                      END"""
        )
    con.commit()
    con.execute("VACUUM")


def _validate_snapshot(con: sqlite3.Connection, src: Path, *, kind: str) -> None:
    tables = {
        str(row[0]) for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if kind == "control":
        required = {
            "tenants",
            "saas_events",
            "retired_tenant_slugs",
            "tenant_subscription_cancellations",
        }
        missing = sorted(required - tables)
        if missing:
            raise RuntimeError(f"control snapshot is missing tables: {', '.join(missing)}")
        tenant_columns = {str(row[1]) for row in con.execute("PRAGMA table_info(tenants)")}
        required_columns = {
            "id",
            "slug",
            "deleted_at",
            "original_slug",
            "tombstone_slug",
            "storage_parked_at",
            "storage_reconciliation_required_at",
            "local_data_purge_started_at",
            "local_data_purged_at",
        }
        if not required_columns <= tenant_columns:
            raise RuntimeError("control snapshot has an incomplete tenant storage schema")
    elif kind == "tenant":
        required = {"schema_migrations", "clients", "projects"}
        missing = sorted(required - tables)
        if missing:
            raise RuntimeError(f"tenant snapshot is missing tables: {', '.join(missing)}")
        migrations = int(con.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0])
        if migrations < 1:
            raise RuntimeError("tenant snapshot has no applied schema migrations")
    else:
        raise ValueError("unknown SQLite snapshot kind")
    ok = con.execute("PRAGMA quick_check").fetchone()[0]
    if ok != "ok":
        raise RuntimeError(f"integrity check failed for {src}: {ok}")
    foreign_key_error = con.execute("PRAGMA foreign_key_check").fetchone()
    if foreign_key_error is not None:
        raise RuntimeError(f"foreign-key check failed for {src}")


def _snapshot_sqlite(src: Path, dest_gz: Path, *, kind: str = "tenant") -> None:
    """Consistent, integrity-checked, gzipped copy of one SQLite database."""
    if src.is_symlink() or not src.is_file():
        raise RuntimeError(f"SQLite backup source is missing or unsafe: {src}")
    dest_gz.parent.mkdir(parents=True, exist_ok=True)
    dest_gz.unlink(missing_ok=True)
    # A power loss/SIGKILL bypasses Python finally blocks. Build the raw copy and
    # partial gzip in an OS temp directory that is never synced, then atomically
    # move only the verified sanitized archive into the backup tree.
    with tempfile.TemporaryDirectory(
        prefix=".mise-staging-",
        dir=dest_gz.parent,
    ) as staging:
        dest_plain = Path(staging) / dest_gz.with_suffix("").name
        # Never give a partial archive the publishable `.db.gz` suffix. This is
        # defense-in-depth beyond the pass lock and rclone staging exclusion.
        staged_gz = Path(staging) / f"{dest_gz.name}.partial"
        try:
            src_con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
            try:
                dst_con = sqlite3.connect(dest_plain)
                try:
                    src_con.backup(dst_con)
                    _scrub_transient_mobile_content(dst_con)
                    _validate_snapshot(dst_con, src, kind=kind)
                finally:
                    dst_con.close()
            finally:
                src_con.close()
            with open(dest_plain, "rb") as f_in, gzip.open(staged_gz, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            staged_gz.replace(dest_gz)
        except Exception:
            dest_gz.unlink(missing_ok=True)
            raise


def _control_inventory(
    control_snapshot_gz: Path,
) -> tuple[list[str], list[tuple[str, str | None]], set[str]]:
    """Read expected live/parked tenant paths from the exact control snapshot."""

    with tempfile.TemporaryDirectory(prefix=".mise-control-inventory-") as staging:
        plain = Path(staging) / "control.db"
        with gzip.open(control_snapshot_gz, "rb") as source, plain.open("wb") as target:
            shutil.copyfileobj(source, target)
        plain.chmod(0o600)
        con = sqlite3.connect(f"file:{plain}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                """SELECT id,slug,deleted_at,original_slug,tombstone_slug,
                          storage_parked_at,storage_reconciliation_required_at,
                          local_data_purge_started_at,
                          local_data_purged_at
                     FROM tenants ORDER BY id"""
            ).fetchall()
        finally:
            con.close()

    live: list[str] = []
    parked: list[tuple[str, str | None]] = []
    remote_cleanup_roots: set[str] = set()
    for row in rows:
        tenant_id = int(row["id"])
        slug = str(row["slug"])
        if Path(slug).name != slug:
            raise RuntimeError(f"tenant {tenant_id} has an unsafe slug path")
        deleted_at = row["deleted_at"]
        if deleted_at is None:
            live.append(slug)
            continue
        if row["storage_reconciliation_required_at"] is not None:
            raise RuntimeError(
                f"tenant {tenant_id} storage identity requires manual reconciliation"
            )
        original_slug = str(row["original_slug"] or "")
        storage_key = str(row["tombstone_slug"] or "")
        if Path(original_slug).name != original_slug or not original_slug:
            raise RuntimeError(f"tenant {tenant_id} has no safe original slug")
        if Path(storage_key).name != storage_key or len(storage_key) > 160:
            raise RuntimeError(f"tenant {tenant_id} has an unsafe storage key")
        internal = re.fullmatch(rf"\.tenant-{tenant_id}-\d{{14}}", storage_key)
        legacy = re.fullmatch(rf".+-deleted-{tenant_id}-\d{{14}}", storage_key)
        if internal is None and legacy is None:
            raise RuntimeError(f"tenant {tenant_id} has an unbound storage key")
        # Keep both identities in the rclone filter even after the local move or
        # approved local purge. Their absence then removes stale remote-current
        # media into versioned history instead of leaving an ambiguous live slug.
        remote_cleanup_roots.add(original_slug)
        remote_cleanup_roots.add(f".trash/{storage_key}")
        if row["local_data_purged_at"] is not None:
            continue
        if row["local_data_purge_started_at"] is not None:
            # The explicit purge arm owns this artifact. The platform backup lock
            # prevents the purge from racing this inventory pass.
            continue
        pending_slug = None if row["storage_parked_at"] is not None else original_slug
        parked.append((storage_key, pending_slug))
    return live, parked, remote_cleanup_roots


def _prune(backups_dir: Path, retention_days: int, *, keep: Path) -> list[str]:
    cutoff = time.time() - retention_days * 86400
    pruned: list[str] = []
    for entry in backups_dir.iterdir():
        if entry == keep:
            continue
        if (
            not entry.is_symlink()
            and entry.is_dir()
            and _PRUNABLE_GENERATION_RE.fullmatch(entry.name)
            and entry.stat().st_mtime < cutoff
        ):
            shutil.rmtree(entry, ignore_errors=True)
            if not entry.exists():
                pruned.append(entry.name)
    return pruned


def _published_generations(backups_dir: Path) -> list[str]:
    """Return only atomically published, manifest-backed generation names."""

    generations: list[str] = []
    if not backups_dir.is_dir():
        return generations
    for entry in sorted(backups_dir.iterdir()):
        if entry.is_symlink() or not entry.is_dir() or not _GENERATION_RE.fullmatch(entry.name):
            continue
        manifest_path = entry / MANIFEST_NAME
        if manifest_path.is_symlink() or not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, ValueError):
            continue
        if (
            manifest.get("format_version") == 1
            and manifest.get("complete") is True
            and manifest.get("stamp") == entry.name
        ):
            generations.append(entry.name)
    return generations


def _offsite_sync(
    remote: str,
    backups_dir: Path,
    tenants_dir: Path,
    stamp: str,
    media_roots: set[str],
) -> str:
    """Sync durable media, copy one immutable generation, commit its manifest last."""
    if not remote:
        return "off"
    if shutil.which("rclone") is None:
        log.error("MISE_BACKUP_RCLONE_REMOTE is set but rclone is not installed")
        return "failed:rclone-missing"
    base = remote.rstrip("/")
    tenant_command = [
        "rclone",
        "sync",
        str(tenants_dir),
        f"{base}/tenants",
        "--backup-dir",
        f"{base}/tenants-history/{stamp}",
    ]
    for pattern in ("**/mise.db*", "**/tmp/**", "**/zips/**"):
        tenant_command.extend(("--filter", f"- {pattern}"))
    for root in sorted(media_roots):
        for durable_root in ("media", "brand", "receipts"):
            tenant_command.extend(("--filter", f"+ /{root}/{durable_root}/**"))
    tenant_command.extend(("--filter", "- **"))
    try:
        subprocess.run(
            tenant_command,
            check=True,
            capture_output=True,
            timeout=6 * 3600,
        )
    except Exception as exc:
        log.error("offsite durable-media sync failed: %s", exc)
        return "failed:tenants"

    generation = backups_dir / stamp
    manifest = backups_dir / stamp / MANIFEST_NAME
    if (
        not generation.is_dir()
        or generation.is_symlink()
        or not manifest.is_file()
        or manifest.is_symlink()
    ):
        return "failed:manifest-missing"
    try:
        manifest_payload = json.loads(manifest.read_text())
    except (OSError, ValueError):
        return "failed:manifest-invalid"
    if (
        manifest_payload.get("format_version") != 1
        or manifest_payload.get("complete") is not True
        or manifest_payload.get("stamp") != stamp
    ):
        return "failed:manifest-invalid"
    try:
        subprocess.run(
            [
                "rclone",
                "copy",
                str(generation),
                f"{base}/backups/{stamp}",
                "--filter",
                f"- {MANIFEST_NAME}",
            ],
            check=True,
            capture_output=True,
            timeout=6 * 3600,
        )
    except Exception as exc:
        log.error("offsite generation payload copy failed: %s", exc)
        return "failed:backups"
    try:
        subprocess.run(
            [
                "rclone",
                "copyto",
                str(manifest),
                f"{base}/backups/{stamp}/{MANIFEST_NAME}",
            ],
            check=True,
            capture_output=True,
            timeout=3600,
        )
    except Exception as exc:
        log.error("offsite generation commit failed: %s", exc)
        return "failed:manifest-commit"
    return "synced"


def run_backup(
    data_dir: Path,
    tenants_dir: Path,
    control_db: Path,
    *,
    retention_days: int = 14,
    rclone_remote: str = "",
    remote_encrypted: bool = False,
) -> dict:
    """Run one exclusive backup pass; overlapping invocations fail closed."""

    if retention_days < 1:
        raise ValueError("backup retention must be at least one day")
    if rclone_remote and not remote_encrypted:
        raise RuntimeError("off-site backup requires an explicit encrypted-remote acknowledgement")

    with _exclusive_backup_lock(data_dir):
        return _run_backup_pass(
            data_dir,
            tenants_dir,
            control_db,
            retention_days=retention_days,
            rclone_remote=rclone_remote,
        )


def _run_backup_pass(
    data_dir: Path,
    tenants_dir: Path,
    control_db: Path,
    *,
    retention_days: int,
    rclone_remote: str,
) -> dict:
    """One lock-held backup pass. Raises only if NOTHING could be backed up."""

    backups_dir = data_dir / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    # Neither per-file nor whole-generation staging is restore material. An
    # abrupt prior process death can leave either; remove it under the pass lock.
    for stale in list(backups_dir.glob(".generation-*")) + list(
        backups_dir.rglob(".mise-staging-*")
    ):
        if stale.is_dir() and not stale.is_symlink():
            shutil.rmtree(stale, ignore_errors=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    dest = backups_dir / stamp
    staging_dest = backups_dir / f".generation-{stamp}"
    staging_dest.mkdir(parents=False, exist_ok=False)

    control_done = False
    tenant_count = 0
    trash_count = 0
    failed_tenants: list[str] = []
    media_roots: set[str] = set()
    expected_live: list[str] = []
    expected_parked: list[tuple[str, str | None]] = []
    remote_cleanup_roots: set[str] = set()
    try:
        if control_db.is_symlink() or not control_db.is_file():
            raise RuntimeError("hosted control database is missing or unsafe")
        control_snapshot = staging_dest / "saas-control.db.gz"
        _snapshot_sqlite(control_db, control_snapshot, kind="control")
        control_done = True
        expected_live, expected_parked, remote_cleanup_roots = _control_inventory(control_snapshot)
        media_roots.update(remote_cleanup_roots)

        tenant_dest = staging_dest / "tenants"
        for slug in expected_live:
            media_roots.add(slug)
            db_file = tenants_dir / slug / "mise.db"
            if db_file.is_symlink() or not db_file.is_file():
                failed_tenants.append(slug)
                continue
            try:
                tenant_dest.mkdir(exist_ok=True)
                _snapshot_sqlite(db_file, tenant_dest / f"{slug}.db.gz")
                tenant_count += 1
            except Exception:
                log.exception("backup failed for tenant %s", slug)
                failed_tenants.append(slug)

        trash_dest = staging_dest / "trash"
        for parked_key, pending_slug in expected_parked:
            parked_dir = tenants_dir / ".trash" / parked_key
            pending_dir = tenants_dir / pending_slug if pending_slug else None
            parked_db = parked_dir / "mise.db"
            if pending_slug is not None:
                # Deletion can rename between inventory and rclone. Allow only
                # both deterministic roots so media survives either phase.
                media_roots.add(pending_slug)
                media_roots.add(f".trash/{parked_key}")
            elif parked_dir.is_dir() and not parked_dir.is_symlink():
                media_roots.add(f".trash/{parked_key}")
            if not parked_db.is_symlink() and parked_db.is_file():
                db_file = parked_db
            elif (
                pending_dir is not None
                and not (pending_dir / "mise.db").is_symlink()
                and (pending_dir / "mise.db").is_file()
            ):
                db_file = pending_dir / "mise.db"
            else:
                failed_tenants.append(f".trash/{parked_key}")
                continue
            try:
                trash_dest.mkdir(exist_ok=True)
                _snapshot_sqlite(db_file, trash_dest / f"{parked_key}.db.gz")
                trash_count += 1
            except Exception:
                log.exception("backup failed for parked tenant %s", parked_key)
                failed_tenants.append(f".trash/{parked_key}")

        manifest = {
            "format_version": 1,
            "complete": True,
            "stamp": stamp,
            "created_at": datetime.now(UTC).isoformat(),
            "control": "saas-control.db.gz",
            "expected_live": expected_live,
            "expected_parked": [key for key, _pending in expected_parked],
            "captured_live": tenant_count,
            "captured_parked": trash_count,
            "failures": failed_tenants,
        }
        manifest_path = staging_dest / MANIFEST_NAME
        with manifest_path.open("x") as handle:
            json.dump(manifest, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        staging_dest.replace(dest)
        _fsync_directory(backups_dir)
    except Exception:
        shutil.rmtree(staging_dest, ignore_errors=True)
        raise

    # Surface partial failure to ops_monitor via a durable file signal (the sidecar
    # runs in its own process, so a return value can't reach the app's monitor). A
    # fully-clean pass clears any stale failure marker so the alert self-resolves.
    failure_marker = backups_dir / FAILURE_MARKER_NAME
    if failed_tenants:
        _atomic_write_text(failure_marker, "\n".join(failed_tenants))
    else:
        _durable_unlink(failure_marker)
    pruned_generations = _prune(backups_dir, retention_days, keep=dest)
    offsite_marker = backups_dir / OFFSITE_FAILURE_MARKER_NAME
    offsite_success_marker = backups_dir / OFFSITE_SUCCESS_MARKER_NAME
    if rclone_remote:
        # Pending is durable before external I/O; a sidecar crash cannot leave a
        # fresh local heartbeat that falsely implies off-site durability.
        _atomic_write_text(
            offsite_marker,
            f"pending {datetime.now(UTC).isoformat()}",
        )
    offsite = _offsite_sync(
        rclone_remote,
        backups_dir,
        tenants_dir,
        stamp,
        media_roots,
    )
    if offsite == "synced":
        _durable_unlink(offsite_marker)
        _atomic_write_text(offsite_success_marker, stamp)
    elif offsite.startswith("failed"):
        _atomic_write_text(
            offsite_marker,
            f"{offsite} {datetime.now(UTC).isoformat()}",
        )
    else:
        _durable_unlink(offsite_marker)
        _durable_unlink(offsite_success_marker)
    # The heartbeat is the commit record for the complete local evidence update.
    # Publish it last, atomically, so SIGKILL cannot pair a fresh heartbeat with
    # stale partial/off-site state.
    marker = backups_dir / MARKER_NAME
    _atomic_write_text(marker, datetime.now(UTC).isoformat())
    summary = {
        "snapshot": str(dest),
        "control": control_done,
        "tenants": tenant_count,
        "parked_tenants": trash_count,
        "tenant_failures": len(failed_tenants),
        "failed": failed_tenants,
        "pruned": len(pruned_generations),
        "offsite": offsite,
    }
    log.info("hosted backup complete: %s", summary)
    return summary
