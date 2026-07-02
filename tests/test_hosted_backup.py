import gzip
import os
import sqlite3
import time
from pathlib import Path

import pytest

from app import config, hosted_backup, ops_monitor

pytestmark = pytest.mark.unit


def _make_db(path: Path, marker_row: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (v TEXT)")
    con.execute("INSERT INTO t VALUES (?)", (marker_row,))
    con.commit()
    con.close()


def _setup(tmp_path: Path) -> tuple[Path, Path, Path]:
    data = tmp_path / "data"
    tenants = data / "tenants"
    control = data / "saas-control.db"
    _make_db(control, "control")
    _make_db(tenants / "alpha" / "mise.db", "alpha-data")
    _make_db(tenants / "beta" / "mise.db", "beta-data")
    return data, tenants, control


def test_backup_snapshots_every_tenant_and_control(tmp_path):
    data, tenants, control = _setup(tmp_path)
    summary = hosted_backup.run_backup(data, tenants, control)
    assert summary["control"] is True
    assert summary["tenants"] == 2 and summary["tenant_failures"] == 0
    assert summary["offsite"] == "off"
    snap = Path(summary["snapshot"])
    assert (snap / "saas-control.db.gz").exists()
    # The snapshot is a real, readable SQLite DB with the tenant's data in it.
    raw = gzip.decompress((snap / "tenants" / "alpha.db.gz").read_bytes())
    assert raw[:16] == b"SQLite format 3\x00"
    restored = tmp_path / "restored.db"
    restored.write_bytes(raw)
    con = sqlite3.connect(restored)
    assert con.execute("SELECT v FROM t").fetchone()[0] == "alpha-data"
    con.close()
    # The heartbeat marker exists for ops_monitor to assert on.
    assert (data / "backups" / hosted_backup.MARKER_NAME).exists()


def test_backup_prunes_beyond_retention_and_skips_trash(tmp_path):
    data, tenants, control = _setup(tmp_path)
    # .trash (deleted-studio parking) must not be snapshotted as a live tenant.
    _make_db(tenants / ".trash" / "gone-deleted-1" / "mise.db", "gone")
    old = data / "backups" / "20200101-000000"
    old.mkdir(parents=True)
    stale = time.time() - 30 * 86400
    os.utime(old, (stale, stale))
    summary = hosted_backup.run_backup(data, tenants, control, retention_days=14)
    assert summary["tenants"] == 2  # alpha + beta, not .trash
    assert summary["pruned"] == 1
    assert not old.exists()


def test_one_broken_tenant_does_not_stop_the_others(tmp_path):
    data, tenants, control = _setup(tmp_path)
    corrupt = tenants / "gamma" / "mise.db"
    corrupt.parent.mkdir(parents=True)
    corrupt.write_bytes(b"this is not a sqlite database at all")
    summary = hosted_backup.run_backup(data, tenants, control)
    assert summary["tenants"] == 2
    assert summary["tenant_failures"] == 1  # loudly counted, never silently dropped


def test_backup_refuses_to_report_success_on_empty_paths(tmp_path):
    with pytest.raises(RuntimeError, match="nothing to back up"):
        hosted_backup.run_backup(
            tmp_path / "data", tmp_path / "data" / "tenants", tmp_path / "data" / "nope.db"
        )


def test_offsite_reports_missing_rclone_as_failure(tmp_path, monkeypatch):
    data, tenants, control = _setup(tmp_path)
    monkeypatch.setattr(hosted_backup.shutil, "which", lambda _: None)
    summary = hosted_backup.run_backup(data, tenants, control, rclone_remote="b2:mise")
    assert summary["offsite"] == "failed:rclone-missing"  # never a silent no-op


def test_ops_monitor_asserts_on_the_hosted_marker(tmp_path, monkeypatch):
    alerts_seen: list[tuple[str, str]] = []
    monkeypatch.setattr(
        ops_monitor.alerts, "ops_alert", lambda sig, msg: alerts_seen.append((sig, msg))
    )
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "BACKUP_STALE_HOURS", 26)
    # No marker ever written -> tenants are unprotected -> alert.
    ops_monitor._check_backup()
    assert alerts_seen and alerts_seen[0][0] == "backup_missing"
    # Fresh marker -> quiet.
    alerts_seen.clear()
    marker = tmp_path / "backups" / hosted_backup.MARKER_NAME
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("now")
    ops_monitor._check_backup()
    assert alerts_seen == []
    # Stale marker -> the sidecar stopped -> alert.
    stale = time.time() - 48 * 3600
    os.utime(marker, (stale, stale))
    ops_monitor._check_backup()
    assert alerts_seen and alerts_seen[0][0] == "backup_stale"
