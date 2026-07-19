import gzip
import os
import sqlite3
import time
from pathlib import Path

import pytest

from app import config, hosted_backup, ops_monitor

pytestmark = pytest.mark.unit


def _make_db(path: Path, marker_row: str, *, tenant_identity: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (v TEXT)")
    con.execute("INSERT INTO t VALUES (?)", (marker_row,))
    if tenant_identity:
        con.execute(
            "CREATE TABLE schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        con.execute("INSERT INTO schema_migrations VALUES ('001_init.sql', datetime('now'))")
    con.commit()
    con.close()


def _add_control_tenant(control: Path, slug: str, *, deleted_at: str | None = None) -> None:
    with sqlite3.connect(control) as con:
        con.execute("CREATE TABLE IF NOT EXISTS tenants (slug TEXT PRIMARY KEY, deleted_at TEXT)")
        con.execute("INSERT INTO tenants VALUES (?, ?)", (slug, deleted_at))


def _setup(tmp_path: Path) -> tuple[Path, Path, Path]:
    data = tmp_path / "data"
    tenants = data / "tenants"
    control = data / "saas-control.db"
    _make_db(control, "control")
    _add_control_tenant(control, "alpha")
    _add_control_tenant(control, "beta")
    _make_db(tenants / "alpha" / "mise.db", "alpha-data", tenant_identity=True)
    _make_db(tenants / "beta" / "mise.db", "beta-data", tenant_identity=True)
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


def test_control_roster_is_read_from_the_exact_archived_snapshot(tmp_path, monkeypatch):
    data, tenants, control = _setup(tmp_path)
    real_roster = hosted_backup._tenant_roster

    def mutate_live_control_then_read_archive(snapshot_con):
        with sqlite3.connect(control) as live:
            live.execute("UPDATE tenants SET deleted_at='2026-07-18' WHERE slug='alpha'")
        return real_roster(snapshot_con)

    monkeypatch.setattr(hosted_backup, "_tenant_roster", mutate_live_control_then_read_archive)

    summary = hosted_backup.run_backup(data, tenants, control)

    assert summary["tenants"] == 2
    archived = tmp_path / "archived-control.db"
    archived.write_bytes(
        gzip.decompress((Path(summary["snapshot"]) / "saas-control.db.gz").read_bytes())
    )
    with sqlite3.connect(archived) as con:
        assert con.execute("SELECT deleted_at FROM tenants WHERE slug='alpha'").fetchone() == (
            None,
        )
    with sqlite3.connect(control) as con:
        assert con.execute("SELECT deleted_at FROM tenants WHERE slug='alpha'").fetchone() == (
            "2026-07-18",
        )


@pytest.mark.parametrize("slug", ["../escape", "Uppercase", "a" * 33])
def test_backup_rejects_invalid_retained_slugs_before_any_path_join(tmp_path, slug):
    data, tenants, control = _setup(tmp_path)
    _add_control_tenant(control, slug)

    with pytest.raises(RuntimeError, match="invalid tenant slug"):
        hosted_backup.run_backup(data, tenants, control)

    backups = data / "backups"
    assert not (backups / hosted_backup.MARKER_NAME).exists()
    assert not (backups / hosted_backup.FAILURE_MARKER_NAME).exists()
    assert not any(path.is_dir() for path in backups.iterdir())
    assert not (data / "escape").exists()


def test_snapshot_failure_removes_plain_and_partial_gzip_artifacts(tmp_path, monkeypatch):
    source = tmp_path / "source.db"
    destination = tmp_path / "snapshot.db.gz"
    _make_db(source, "source")

    def fail_after_partial_write(_source, target):
        target.write(b"partial")
        raise OSError("simulated snapshot write failure")

    monkeypatch.setattr(hosted_backup.shutil, "copyfileobj", fail_after_partial_write)

    with pytest.raises(OSError, match="simulated snapshot write failure"):
        hosted_backup._snapshot_sqlite(source, destination)

    assert not destination.exists()
    assert not destination.with_suffix("").exists()


def test_backup_prunes_beyond_retention_and_ignores_real_tombstones(tmp_path):
    data, tenants, control = _setup(tmp_path)
    max_slug = "a" * 32
    tombstone = f"{'b' * 32}-deleted-1234-20260718123456"
    _add_control_tenant(control, max_slug)
    _make_db(tenants / max_slug / "mise.db", "max-live", tenant_identity=True)
    _add_control_tenant(control, tombstone, deleted_at="2026-07-18")
    _make_db(tenants / ".trash" / tombstone / "mise.db", "gone")
    _make_db(tenants / "orphan" / "mise.db", "orphan", tenant_identity=True)
    old = data / "backups" / "20200101-000000"
    old.mkdir(parents=True)
    stale = time.time() - 30 * 86400
    os.utime(old, (stale, stale))

    summary = hosted_backup.run_backup(data, tenants, control, retention_days=14)

    snapshot_tenants = Path(summary["snapshot"]) / "tenants"
    assert summary["tenants"] == 3  # alpha + beta + max-length retained slug
    assert (snapshot_tenants / f"{max_slug}.db.gz").is_file()
    assert not (snapshot_tenants / f"{tombstone}.db.gz").exists()
    assert not (snapshot_tenants / "orphan.db.gz").exists()
    assert summary["pruned"] == 1
    assert not old.exists()


def test_one_broken_tenant_does_not_stop_the_others(tmp_path):
    data, tenants, control = _setup(tmp_path)
    _add_control_tenant(control, "gamma")
    corrupt = tenants / "gamma" / "mise.db"
    corrupt.parent.mkdir(parents=True)
    corrupt.write_bytes(b"this is not a sqlite database at all")
    summary = hosted_backup.run_backup(data, tenants, control)
    assert summary["tenants"] == 2
    assert summary["tenant_failures"] == 1  # loudly counted, never silently dropped
    assert summary["failed"] == ["gamma"]


def test_partial_failure_writes_marker_and_a_clean_pass_clears_it(tmp_path):
    # A per-tenant failure must leave a durable signal (the heartbeat marker alone
    # can't express "some studios have no fresh snapshot"), and it must self-resolve
    # once the tenant backs up cleanly again.
    data, tenants, control = _setup(tmp_path)
    _add_control_tenant(control, "gamma")
    corrupt = tenants / "gamma" / "mise.db"
    corrupt.parent.mkdir(parents=True)
    corrupt.write_bytes(b"not a database")
    fmarker = data / "backups" / hosted_backup.FAILURE_MARKER_NAME

    hosted_backup.run_backup(data, tenants, control)
    assert fmarker.exists() and "gamma" in fmarker.read_text()

    # Heal the tenant; the next clean pass clears the failure signal.
    corrupt.unlink()
    _make_db(corrupt, "gamma-data", tenant_identity=True)
    summary = hosted_backup.run_backup(data, tenants, control)
    assert summary["tenant_failures"] == 0 and summary["failed"] == []
    assert not fmarker.exists()


def test_missing_retained_tenant_is_a_durable_partial_failure(tmp_path):
    data, tenants, control = _setup(tmp_path)
    _add_control_tenant(control, "gamma")

    summary = hosted_backup.run_backup(data, tenants, control)

    assert summary["tenants"] == 2
    assert summary["tenant_failures"] == 1
    assert summary["failed"] == ["gamma"]
    assert not (tenants / "gamma").exists()
    failure_marker = data / "backups" / hosted_backup.FAILURE_MARKER_NAME
    assert failure_marker.read_text() == "gamma"


@pytest.mark.parametrize("shape", ["zero-byte", "unrelated", "empty-marker"])
def test_backup_rejects_tenant_files_the_runtime_would_refuse_without_mutating_them(
    tmp_path, shape
):
    data, tenants, control = _setup(tmp_path)
    _add_control_tenant(control, "gamma")
    path = tenants / "gamma" / "mise.db"
    path.parent.mkdir()
    if shape == "zero-byte":
        path.touch()
    elif shape == "unrelated":
        _make_db(path, "not-a-tenant")
    else:
        with sqlite3.connect(path) as con:
            con.execute(
                "CREATE TABLE schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
    before = path.read_bytes()

    summary = hosted_backup.run_backup(data, tenants, control)

    assert summary["tenants"] == 2
    assert summary["failed"] == ["gamma"]
    assert path.read_bytes() == before
    assert not (Path(summary["snapshot"]) / "tenants" / "gamma.db.gz").exists()


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


def test_ops_monitor_alerts_on_a_partial_backup_by_name(tmp_path, monkeypatch):
    alerts_seen: list[tuple[str, str]] = []
    monkeypatch.setattr(
        ops_monitor.alerts, "ops_alert", lambda sig, msg: alerts_seen.append((sig, msg))
    )
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "BACKUP_STALE_HOURS", 26)
    bdir = tmp_path / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    (bdir / hosted_backup.MARKER_NAME).write_text("now")  # heartbeat fresh

    # Fresh heartbeat but a tenant was skipped -> surfaced by name, not hidden.
    (bdir / hosted_backup.FAILURE_MARKER_NAME).write_text("gamma\ndelta")
    ops_monitor._check_backup()
    assert len(alerts_seen) == 1
    sig, msg = alerts_seen[0]
    assert sig == "backup_partial" and "gamma" in msg and "delta" in msg and "2 tenant" in msg

    # A clean pass removed the failure marker -> quiet again.
    alerts_seen.clear()
    (bdir / hosted_backup.FAILURE_MARKER_NAME).unlink()
    ops_monitor._check_backup()
    assert alerts_seen == []
