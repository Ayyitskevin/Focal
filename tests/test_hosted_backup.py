import fcntl
import gzip
import importlib.util
import os
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest

from app import config, hosted_backup, ops_monitor

pytestmark = pytest.mark.unit


def _add_tenant_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE schema_migrations (filename TEXT PRIMARY KEY);
        INSERT INTO schema_migrations VALUES ('001_initial.sql');
        CREATE TABLE clients (id INTEGER PRIMARY KEY);
        CREATE TABLE projects (id INTEGER PRIMARY KEY);
        """
    )


def _make_db(path: Path, marker_row: str, *, tenant: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (v TEXT)")
    con.execute("INSERT INTO t VALUES (?)", (marker_row,))
    if tenant:
        _add_tenant_schema(con)
    con.commit()
    con.close()


def _setup(tmp_path: Path) -> tuple[Path, Path, Path]:
    data = tmp_path / "data"
    tenants = data / "tenants"
    control = data / "saas-control.db"
    _make_db(control, "control", tenant=False)
    with sqlite3.connect(control) as con:
        con.executescript(
            """CREATE TABLE tenants (
                   id INTEGER PRIMARY KEY,
                   slug TEXT NOT NULL,
                   deleted_at TEXT,
                   original_slug TEXT,
                   tombstone_slug TEXT,
                   storage_parked_at TEXT,
                   storage_reconciliation_required_at TEXT,
                   local_data_purge_started_at TEXT,
                   local_data_purged_at TEXT
               );
               CREATE TABLE saas_events (id TEXT PRIMARY KEY,type TEXT);
               CREATE TABLE retired_tenant_slugs (
                   slug TEXT PRIMARY KEY,tenant_id INTEGER,retired_at TEXT
               );
               CREATE TABLE tenant_subscription_cancellations (
                   tenant_id INTEGER,subscription_id TEXT,state TEXT,
                   discovered_at TEXT,attempted_at TEXT,succeeded_at TEXT,
                   PRIMARY KEY (tenant_id,subscription_id)
               );"""
        )
        con.executemany(
            """INSERT INTO tenants (id,slug,deleted_at,original_slug)
               VALUES (?,?,NULL,?)""",
            ((1, "alpha", "alpha"), (2, "beta", "beta")),
        )
    _make_db(tenants / "alpha" / "mise.db", "alpha-data")
    _make_db(tenants / "beta" / "mise.db", "beta-data")
    return data, tenants, control


def _add_control_tenant(
    control: Path,
    slug: str,
    *,
    tenant_id: int,
    deleted_at: str | None = None,
) -> None:
    original_slug = slug
    storage_key = None
    storage_parked_at = None
    if deleted_at is not None:
        suffix = f"-deleted-{tenant_id}-"
        if suffix in slug:
            original_slug = slug.split(suffix, 1)[0]
            storage_key = slug
        else:
            storage_key = f".tenant-{tenant_id}-20260101000000"
        storage_parked_at = deleted_at
    with sqlite3.connect(control) as con:
        con.execute(
            """INSERT INTO tenants
               (id,slug,deleted_at,original_slug,tombstone_slug,storage_parked_at)
               VALUES (?,?,?,?,?,?)""",
            (
                tenant_id,
                slug,
                deleted_at,
                original_slug,
                storage_key,
                storage_parked_at,
            ),
        )


def test_backup_snapshots_every_tenant_and_control(tmp_path):
    data, tenants, control = _setup(tmp_path)
    summary = hosted_backup.run_backup(data, tenants, control)
    assert summary["control"] is True
    assert summary["tenants"] == 2 and summary["tenant_failures"] == 0
    assert summary["offsite"] == "off"
    snap = Path(summary["snapshot"])
    assert (snap / "saas-control.db.gz").exists()
    assert (snap / hosted_backup.MANIFEST_NAME).exists()
    assert hosted_backup._published_generations(data / "backups") == [snap.name]
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


def test_backup_rejects_an_overlapping_pass_before_touching_snapshots(tmp_path):
    data, tenants, control = _setup(tmp_path)
    data.mkdir(parents=True, exist_ok=True)
    with (data / hosted_backup.LOCK_NAME).open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="already running"):
            hosted_backup.run_backup(data, tenants, control)

    assert not (data / "backups").exists()


def test_sqlite_snapshot_scrubs_and_compacts_transient_caption_content(tmp_path):
    live = tmp_path / "live.db"
    snapshot = tmp_path / "snapshot.db.gz"
    instruction = "BACKUP_PRIVATE_OWNER_INSTRUCTION_7d4b"
    candidate = "BACKUP_PRIVATE_PROVIDER_CANDIDATE_1c9a"
    with sqlite3.connect(live) as con:
        _add_tenant_schema(con)
        con.execute(
            """CREATE TABLE mobile_caption_suggestions (
                   id TEXT PRIMARY KEY,session_id TEXT,status TEXT,context_json TEXT,
                   candidate_text TEXT,provider TEXT,model TEXT,failure_code TEXT,
                   completed_at TEXT
               )"""
        )
        con.execute(
            """INSERT INTO mobile_caption_suggestions
               VALUES ('one','session','ready',?,?,'provider','model',NULL,NULL)""",
            (f'{{"instruction":"{instruction}"}}', candidate),
        )
        con.execute(
            """CREATE TABLE mobile_caption_usage (
                   id TEXT PRIMARY KEY,state TEXT,accepted_at TEXT,
                   finished_at TEXT
               )"""
        )
        con.execute(
            """INSERT INTO mobile_caption_usage
               VALUES ('one','active','2026-07-11T12:00:00Z',NULL)"""
        )
        con.executescript(
            """
            CREATE TABLE api_sessions (id TEXT,revoked_at INTEGER,revoke_reason TEXT);
            INSERT INTO api_sessions VALUES ('session',NULL,NULL);
            CREATE TABLE api_tokens (id INTEGER,revoked_at INTEGER);
            INSERT INTO api_tokens VALUES (1,NULL);
            CREATE TABLE mobile_push_devices (
                id INTEGER,session_id TEXT,token_ciphertext TEXT,active INTEGER,
                disabled_reason TEXT,disabled_at TEXT,updated_at TEXT
            );
            INSERT INTO mobile_push_devices
            VALUES (1,'session','PRIVATE_TOKEN',1,NULL,NULL,datetime('now'));
            CREATE TABLE mobile_notification_deliveries (
                id INTEGER,status TEXT,claim_token TEXT,claimed_at TEXT,
                queued_job_id INTEGER,reason TEXT,updated_at TEXT
            );
            INSERT INTO mobile_notification_deliveries
            VALUES (1,'sending','claim',datetime('now'),1,NULL,datetime('now'));
            CREATE TABLE jobs (
                id INTEGER,kind TEXT,status TEXT,error TEXT,updated_at TEXT
            );
            INSERT INTO jobs VALUES (1,'apns_delivery','running',NULL,datetime('now'));
            """
        )

    hosted_backup._snapshot_sqlite(live, snapshot)

    # The source-of-truth live row is unchanged; only restore material is scrubbed.
    with sqlite3.connect(live) as con:
        assert (
            con.execute("SELECT candidate_text FROM mobile_caption_suggestions").fetchone()[0]
            == candidate
        )
    raw = gzip.decompress(snapshot.read_bytes())
    assert instruction.encode() not in raw
    assert candidate.encode() not in raw
    restored = tmp_path / "restored-scrubbed.db"
    restored.write_bytes(raw)
    with sqlite3.connect(restored) as con:
        row = con.execute("SELECT * FROM mobile_caption_suggestions").fetchone()
        assert row[1] is None
        assert row[2] == "failed"
        assert row[3] is None and row[4] is None
        assert row[5] is None and row[6] is None
        assert row[7] == "session_ended" and row[8] is not None
        usage = con.execute("SELECT * FROM mobile_caption_usage").fetchone()
        assert usage[1] == "finished" and usage[2] == "2026-07-11T12:00:00Z"
        assert usage[3] is not None
        session = con.execute("SELECT revoked_at,revoke_reason FROM api_sessions").fetchone()
        token = con.execute("SELECT revoked_at FROM api_tokens").fetchone()
        device = con.execute(
            "SELECT active,session_id,token_ciphertext,disabled_reason FROM mobile_push_devices"
        ).fetchone()
        delivery = con.execute(
            "SELECT status,claim_token,queued_job_id,reason FROM mobile_notification_deliveries"
        ).fetchone()
        job = con.execute("SELECT status,error FROM jobs").fetchone()
        assert session[0] is not None and session[1] == "backup_restore"
        assert token[0] is not None
        assert tuple(device) == (0, None, None, "backup_restore")
        assert tuple(delivery) == ("failed", None, None, "backup_restore")
        assert tuple(job) == ("failed", "backup_restore")


def test_sqlite_snapshot_scrub_failure_leaves_no_uploadable_artifact(tmp_path, monkeypatch):
    live = tmp_path / "live.db"
    snapshot = tmp_path / "snapshot.db.gz"
    _make_db(live, "PRIVATE_SNAPSHOT_SENTINEL")

    def fail_scrub(_con):
        raise RuntimeError("forced sanitizer failure")

    monkeypatch.setattr(hosted_backup, "_scrub_transient_mobile_content", fail_scrub)

    with pytest.raises(RuntimeError, match="forced sanitizer failure"):
        hosted_backup._snapshot_sqlite(live, snapshot)

    assert not snapshot.exists()
    assert not snapshot.with_suffix("").exists()
    assert not any(
        b"PRIVATE_SNAPSHOT_SENTINEL" in path.read_bytes()
        for path in tmp_path.iterdir()
        if path != live and path.is_file()
    )


def test_single_tenant_script_scrub_failure_removes_raw_snapshot(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "mise.db").write_bytes(b"live-placeholder")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_sqlite = fake_bin / "sqlite3"
    fake_sqlite.write_text(
        """#!/usr/bin/env python3
import pathlib
import re
import sys
sql = sys.argv[2]
if sql.startswith('.backup'):
    match = re.search(r"'([^']+)'", sql)
    pathlib.Path(match.group(1)).write_bytes(b'PRIVATE_UNSANITIZED_SNAPSHOT')
elif 'SELECT COUNT(*) FROM sqlite_master' in sql:
    print('1')
else:
    raise SystemExit(42)
"""
    )
    fake_sqlite.chmod(0o755)
    script = Path(__file__).resolve().parents[1] / "ops" / "backup.sh"
    result = subprocess.run(
        ["bash", str(script)],
        env={
            **os.environ,
            "MISE_DATA_DIR": str(data),
            "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 42
    backups = data / "backups"
    assert list(backups.iterdir()) == []


def test_single_tenant_script_rejects_overlapping_pass_before_staging_cleanup(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    staging = data / ".backup-staging"
    staging.mkdir()
    sentinel = staging / "active-pass.tmp"
    sentinel.write_text("must survive")
    script = Path(__file__).resolve().parents[1] / "ops" / "backup.sh"

    with (data / ".backup.lock").open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subprocess.run(
            ["bash", str(script)],
            env={**os.environ, "MISE_DATA_DIR": str(data)},
            capture_output=True,
            text=True,
            check=False,
        )

    assert result.returncode == 75
    assert "already running" in result.stderr
    assert sentinel.read_text() == "must survive"


def test_backup_prunes_beyond_retention_and_skips_trash(tmp_path):
    data, tenants, control = _setup(tmp_path)
    # .trash (deleted-studio parking) must not be snapshotted as a live tenant.
    parked_key = "gone-deleted-3-20260101000000"
    _add_control_tenant(
        control,
        parked_key,
        tenant_id=3,
        deleted_at="2026-01-01T00:00:00+00:00",
    )
    _make_db(tenants / ".trash" / parked_key / "mise.db", "gone")
    old = data / "backups" / "20200101-000000-000000"
    old.mkdir(parents=True)
    legacy_old = data / "backups" / "20200101-000001"
    legacy_old.mkdir(parents=True)
    stale = time.time() - 30 * 86400
    os.utime(old, (stale, stale))
    os.utime(legacy_old, (stale, stale))
    summary = hosted_backup.run_backup(data, tenants, control, retention_days=14)
    assert summary["tenants"] == 2  # alpha + beta, not .trash
    assert summary["parked_tenants"] == 1
    assert (Path(summary["snapshot"]) / "trash" / f"{parked_key}.db.gz").exists()
    assert summary["pruned"] == 2
    assert not old.exists() and not legacy_old.exists()


def test_one_broken_tenant_does_not_stop_the_others(tmp_path):
    data, tenants, control = _setup(tmp_path)
    _add_control_tenant(control, "gamma", tenant_id=3)
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
    _add_control_tenant(control, "gamma", tenant_id=3)
    corrupt = tenants / "gamma" / "mise.db"
    corrupt.parent.mkdir(parents=True)
    corrupt.write_bytes(b"not a database")
    fmarker = data / "backups" / hosted_backup.FAILURE_MARKER_NAME

    hosted_backup.run_backup(data, tenants, control)
    assert fmarker.exists() and "gamma" in fmarker.read_text()

    # Heal the tenant; the next clean pass clears the failure signal.
    corrupt.unlink()
    _make_db(corrupt, "gamma-data")
    summary = hosted_backup.run_backup(data, tenants, control)
    assert summary["tenant_failures"] == 0 and summary["failed"] == []
    assert not fmarker.exists()


def test_backup_refuses_to_report_success_on_empty_paths(tmp_path):
    with pytest.raises(RuntimeError, match="control database is missing"):
        hosted_backup.run_backup(
            tmp_path / "data", tmp_path / "data" / "tenants", tmp_path / "data" / "nope.db"
        )


def test_empty_or_symlinked_expected_tenant_database_is_a_partial_failure(
    tmp_path,
):
    data, tenants, control = _setup(tmp_path)
    (tenants / "alpha" / "mise.db").unlink()
    sqlite3.connect(tenants / "alpha" / "mise.db").close()
    (tenants / "beta-link").mkdir()
    (tenants / "beta-link" / "mise.db").symlink_to(tenants / "beta" / "mise.db")
    _add_control_tenant(control, "beta-link", tenant_id=3)

    summary = hosted_backup.run_backup(data, tenants, control)

    assert summary["tenants"] == 1
    assert set(summary["failed"]) == {"alpha", "beta-link"}


def test_interrupted_generation_is_hidden_and_never_published_or_synced(
    tmp_path,
    monkeypatch,
):
    data, tenants, control = _setup(tmp_path)
    original = hosted_backup._snapshot_sqlite

    class SimulatedProcessDeath(BaseException):
        pass

    def die_on_first_tenant(source, destination, *, kind="tenant"):
        if kind == "tenant":
            raise SimulatedProcessDeath
        original(source, destination, kind=kind)

    monkeypatch.setattr(hosted_backup, "_snapshot_sqlite", die_on_first_tenant)
    with pytest.raises(SimulatedProcessDeath):
        hosted_backup.run_backup(data, tenants, control)

    backups = data / "backups"
    assert hosted_backup._published_generations(backups) == []
    assert not list(backups.glob("20*"))
    assert list(backups.glob(".generation-*"))

    monkeypatch.setattr(hosted_backup, "_snapshot_sqlite", original)
    summary = hosted_backup.run_backup(data, tenants, control)
    assert not list(backups.glob(".generation-*"))
    assert hosted_backup._published_generations(backups) == [Path(summary["snapshot"]).name]


@pytest.mark.parametrize("raw", ["0", "-1", "nan", "inf", "169", "bad"])
def test_hosted_backup_loop_rejects_unsafe_intervals(raw):
    script = Path(__file__).resolve().parents[1] / "scripts" / "hosted-backup.py"
    spec = importlib.util.spec_from_file_location("mise_hosted_backup_cli", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(ValueError, match="interval"):
        module._interval_seconds(raw)


def test_single_tenant_script_rejects_missing_or_symlinked_source(tmp_path):
    script = Path(__file__).resolve().parents[1] / "ops" / "backup.sh"
    data = tmp_path / "missing"
    data.mkdir()
    missing = subprocess.run(
        ["bash", str(script)],
        env={**os.environ, "MISE_DATA_DIR": str(data)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing.returncode == 1 and "missing or is a symlink" in missing.stderr

    target = tmp_path / "real.db"
    target.write_bytes(b"not relevant")
    (data / "mise.db").symlink_to(target)
    symlinked = subprocess.run(
        ["bash", str(script)],
        env={**os.environ, "MISE_DATA_DIR": str(data)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert symlinked.returncode == 1 and "missing or is a symlink" in symlinked.stderr


def test_offsite_reports_missing_rclone_as_failure(tmp_path, monkeypatch):
    data, tenants, control = _setup(tmp_path)
    monkeypatch.setattr(hosted_backup.shutil, "which", lambda _: None)
    summary = hosted_backup.run_backup(
        data,
        tenants,
        control,
        rclone_remote="b2:mise",
        remote_encrypted=True,
    )
    assert summary["offsite"] == "failed:rclone-missing"  # never a silent no-op
    assert (data / "backups" / hosted_backup.OFFSITE_FAILURE_MARKER_NAME).exists()


def test_control_inventory_surfaces_missing_tenant_database(tmp_path):
    data, tenants, control = _setup(tmp_path)
    (tenants / "beta" / "mise.db").unlink()

    summary = hosted_backup.run_backup(data, tenants, control)

    assert summary["tenants"] == 1
    assert summary["failed"] == ["beta"]
    marker = data / "backups" / hosted_backup.FAILURE_MARKER_NAME
    assert marker.read_text() == "beta"


def test_offsite_allowlist_excludes_staging_and_live_sqlite(tmp_path, monkeypatch):
    data, tenants, control = _setup(tmp_path)
    _make_db(tenants / "orphan" / "mise.db", "must-not-sync")
    commands = []
    monkeypatch.setattr(hosted_backup.shutil, "which", lambda _: "/usr/bin/rclone")
    monkeypatch.setattr(
        hosted_backup.subprocess,
        "run",
        lambda command, **_kwargs: commands.append(command),
    )

    summary = hosted_backup.run_backup(
        data,
        tenants,
        control,
        rclone_remote="remote:mise",
        remote_encrypted=True,
    )

    assert summary["offsite"] == "synced"
    backup_command = next(command for command in commands if command[1] == "copy")
    tenant_command = next(command for command in commands if "remote:mise/tenants" in command)
    backup_text = " ".join(backup_command)
    tenant_text = " ".join(tenant_command)
    assert backup_command[2] == summary["snapshot"]
    assert backup_command[3] == f"remote:mise/backups/{Path(summary['snapshot']).name}"
    assert backup_command[-2:] == ["--filter", f"- {hosted_backup.MANIFEST_NAME}"]
    assert hosted_backup.OFFSITE_FAILURE_MARKER_NAME not in backup_text
    assert "- **/mise.db*" in tenant_text
    assert "- **/tmp/**" in tenant_text and "- **/zips/**" in tenant_text
    assert "+ /alpha/media/**" in tenant_text and "+ /beta/brand/**" in tenant_text
    assert "+ /alpha/**" not in tenant_text and "+ /orphan/media/**" not in tenant_text
    assert not (Path(summary["snapshot"]) / "tenants" / "orphan.db.gz").exists()
    assert not (data / "backups" / hosted_backup.OFFSITE_FAILURE_MARKER_NAME).exists()
    assert (data / "backups" / hosted_backup.OFFSITE_SUCCESS_MARKER_NAME).exists()


@pytest.mark.parametrize("retention_days", [0, -1])
def test_invalid_retention_never_prunes_or_publishes(tmp_path, retention_days):
    data, tenants, control = _setup(tmp_path)

    with pytest.raises(ValueError, match="at least one day"):
        hosted_backup.run_backup(
            data,
            tenants,
            control,
            retention_days=retention_days,
        )

    assert not (data / "backups" / hosted_backup.MARKER_NAME).exists()


def test_heartbeat_is_not_published_before_offsite_evidence_commits(
    tmp_path,
    monkeypatch,
):
    data, tenants, control = _setup(tmp_path)

    class SimulatedProcessDeath(BaseException):
        pass

    monkeypatch.setattr(
        hosted_backup,
        "_offsite_sync",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SimulatedProcessDeath()),
    )

    with pytest.raises(SimulatedProcessDeath):
        hosted_backup.run_backup(
            data,
            tenants,
            control,
            rclone_remote="crypt:backup",
            remote_encrypted=True,
        )

    assert not (data / "backups" / hosted_backup.MARKER_NAME).exists()
    assert (data / "backups" / hosted_backup.OFFSITE_FAILURE_MARKER_NAME).exists()


def test_pending_delete_media_allowlist_covers_both_sides_of_rename(
    tmp_path,
    monkeypatch,
):
    data, tenants, control = _setup(tmp_path)
    deleted_at = "2026-07-11T12:00:00+00:00"
    with sqlite3.connect(control) as con:
        con.execute(
            """UPDATE tenants
                  SET deleted_at=?,original_slug='alpha',
                      tombstone_slug='.tenant-1-20260711120000',
                      storage_parked_at=NULL
                WHERE id=1""",
            (deleted_at,),
        )
    parked_key = ".tenant-1-20260711120000"
    captured = {}
    original_snapshot = hosted_backup._snapshot_sqlite

    def snapshot_then_move(source, destination, *, kind="tenant"):
        original_snapshot(source, destination, kind=kind)
        if source == tenants / "alpha" / "mise.db":
            parked = tenants / ".trash" / parked_key
            parked.parent.mkdir(parents=True, exist_ok=True)
            (tenants / "alpha").rename(parked)

    def capture_sync(_remote, _backups, _tenants, _stamp, media_roots):
        captured["roots"] = media_roots
        return "synced"

    monkeypatch.setattr(hosted_backup, "_snapshot_sqlite", snapshot_then_move)
    monkeypatch.setattr(hosted_backup, "_offsite_sync", capture_sync)

    hosted_backup.run_backup(
        data,
        tenants,
        control,
        rclone_remote="crypt:backup",
        remote_encrypted=True,
    )

    assert {"alpha", f".trash/{parked_key}"} <= captured["roots"]


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


def test_ops_monitor_alerts_on_offsite_failure_despite_fresh_local_snapshot(
    tmp_path,
    monkeypatch,
):
    alerts_seen: list[tuple[str, str]] = []
    monkeypatch.setattr(
        ops_monitor.alerts,
        "ops_alert",
        lambda sig, msg: alerts_seen.append((sig, msg)),
    )
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "BACKUP_STALE_HOURS", 26)
    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / hosted_backup.MARKER_NAME).write_text("fresh local")
    (backups / hosted_backup.OFFSITE_FAILURE_MARKER_NAME).write_text("failed:rclone")

    ops_monitor._check_backup()

    assert [signature for signature, _message in alerts_seen] == ["backup_offsite_failed"]
