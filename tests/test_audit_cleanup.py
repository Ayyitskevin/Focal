"""Pre-launch audit — the low-severity cleanup batch (#5–#8).

#5 delete_tenant_studio: a swallowed Stripe cancel now stamps + surfaces in the
   console instead of silently billing a departed studio.
#6 db.migrate: bare-DDL migrations apply atomically, so an interrupted apply
   rolls back cleanly instead of bricking the DB on retry (075's DROP COLUMNs).
#7 portal PIN lockout: buckets are offset off the inquiry-throttle sentinels.
#8 hosted_backup off-site sync: --backup-dir versions the mirror so a local
   deletion/corruption isn't propagated as a destroy on the remote.
"""

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest
from starlette.requests import Request

from app import config, db, hosted_backup, saas, security
from app.public import portal

pytestmark = pytest.mark.unit


# ───────────────────────────── #7 portal PIN bucket namespace ─────────────────────────────


def test_portal_pin_bucket_does_not_collide_with_inquiry_sentinels():
    ip = "203.0.113.7"
    # Portal id 2 used to map to bucket -2 == INQUIRY_BUCKET_CONTACT. It now offsets.
    bucket = portal.PIN_OFFSET + 2
    # Its own positive band: clear of the inquiry sentinels (-2..-5) and distinct
    # from the workspace offset (2_000_000).
    assert bucket > 0
    assert bucket not in {
        security.INQUIRY_BUCKET_CONTACT,
        security.INQUIRY_BUCKET_BOOK,
        security.INQUIRY_BUCKET_FORM,
        security.INQUIRY_BUCKET_PACKAGE,
    }

    # Exhaust the portal PIN lockout on portal 2...
    for _ in range(config.PIN_MAX_FAILS):
        security.pin_fail(ip, bucket)
    assert security.pin_locked(ip, bucket) is True
    # ...and the public /contact inquiry throttle for the same IP is untouched.
    assert security.inquiry_throttled(ip, security.INQUIRY_BUCKET_CONTACT) is False

    # Conversely, hammering the inquiry throttle must not lock the portal.
    ip2 = "203.0.113.8"
    b2 = portal.PIN_OFFSET + 3
    for _ in range(security.INQUIRY_MAX_PER_WINDOW + 2):
        security.inquiry_record(ip2, security.INQUIRY_BUCKET_BOOK)
    assert security.pin_locked(ip2, b2) is False


# ───────────────────────────── #8 versioned off-site sync ─────────────────────────────


def test_offsite_sync_versions_media_and_commits_generation_manifest_last(
    tmp_path,
    monkeypatch,
):
    calls: list[list[str]] = []
    monkeypatch.setattr(hosted_backup.shutil, "which", lambda _: "/usr/bin/rclone")
    generation = "20260703-000000-000001"
    generation_dir = tmp_path / "backups" / generation
    generation_dir.mkdir(parents=True)
    (generation_dir / hosted_backup.MANIFEST_NAME).write_text(
        json.dumps(
            {
                "format_version": 1,
                "complete": True,
                "stamp": generation,
            }
        )
    )

    def fake_run(argv, **kw):
        calls.append(argv)

        class R:
            returncode = 0

        return R()

    monkeypatch.setattr(hosted_backup.subprocess, "run", fake_run)
    status = hosted_backup._offsite_sync(
        "b2:mise",
        tmp_path / "backups",
        tmp_path / "tenants",
        generation,
        {"alpha", ".trash/gone"},
    )
    assert status == "synced"
    # Both trees synced, each with a timestamped --backup-dir so overwritten/deleted
    # remote files are MOVED to history, never destroyed.
    tenant_command = next(argv for argv in calls if argv[1] == "sync")
    assert tenant_command[3] == "b2:mise/tenants"
    assert tenant_command[tenant_command.index("--backup-dir") + 1] == (
        f"b2:mise/tenants-history/{generation}"
    )
    tenant_filters = [
        tenant_command[index + 1]
        for index, value in enumerate(tenant_command)
        if value == "--filter"
    ]
    assert tenant_filters == [
        "- **/mise.db*",
        "- **/tmp/**",
        "- **/zips/**",
        "+ /.trash/gone/media/**",
        "+ /.trash/gone/brand/**",
        "+ /.trash/gone/receipts/**",
        "+ /alpha/media/**",
        "+ /alpha/brand/**",
        "+ /alpha/receipts/**",
        "- **",
    ]
    payload_copy = next(argv for argv in calls if argv[1] == "copy")
    assert payload_copy[2] == str(generation_dir)
    assert payload_copy[3] == f"b2:mise/backups/{generation}"
    assert payload_copy[-2:] == ["--filter", f"- {hosted_backup.MANIFEST_NAME}"]
    commit = next(argv for argv in calls if argv[1] == "copyto")
    assert commit[-1] == f"b2:mise/backups/{generation}/{hosted_backup.MANIFEST_NAME}"
    assert calls.index(tenant_command) < calls.index(payload_copy) < calls.index(commit)


@pytest.mark.parametrize(
    ("failing_command", "expected_status", "expected_commands"),
    [
        ("sync", "failed:tenants", ["sync"]),
        ("copy", "failed:backups", ["sync", "copy"]),
        (
            "copyto",
            "failed:manifest-commit",
            ["sync", "copy", "copyto"],
        ),
    ],
)
def test_offsite_failure_boundaries_never_publish_manifest_early_or_touch_history(
    tmp_path,
    monkeypatch,
    failing_command,
    expected_status,
    expected_commands,
):
    calls: list[list[str]] = []
    monkeypatch.setattr(hosted_backup.shutil, "which", lambda _: "/usr/bin/rclone")
    current = "20260703-000000-000002"
    previous = "20260702-000000-000001"
    generation_dir = tmp_path / "backups" / current
    generation_dir.mkdir(parents=True)
    (generation_dir / hosted_backup.MANIFEST_NAME).write_text(
        json.dumps(
            {
                "format_version": 1,
                "complete": True,
                "stamp": current,
            }
        )
    )
    (tmp_path / "backups" / previous).mkdir()

    def fail_at_boundary(argv, **_kwargs):
        calls.append(argv)
        if argv[1] == failing_command:
            raise hosted_backup.subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(hosted_backup.subprocess, "run", fail_at_boundary)

    status = hosted_backup._offsite_sync(
        "crypt:mise",
        tmp_path / "backups",
        tmp_path / "tenants",
        current,
        {"alpha"},
    )

    assert status == expected_status
    assert [argv[1] for argv in calls] == expected_commands
    if failing_command != "copyto":
        assert all(argv[1] != "copyto" for argv in calls)
    assert all(previous not in " ".join(argv) for argv in calls)
    payload_sources = [argv[2] for argv in calls if argv[1] == "copy"]
    assert payload_sources in ([], [str(generation_dir)])


# ───────────────────────────── #6 atomic migration apply ─────────────────────────────


def _write_migration(mdir: Path, name: str, body: str):
    (mdir / name).write_text(body)


def test_interrupted_migration_rolls_back_and_reruns_clean(tmp_path, monkeypatch):
    mdir = tmp_path / "migrations"
    mdir.mkdir()
    monkeypatch.setattr(db, "MIGRATIONS_DIR", mdir)
    dbfile = tmp_path / "t.db"

    # A bare-DDL migration whose 2nd statement is invalid: the 1st must NOT persist.
    _write_migration(mdir, "001_x.sql", "CREATE TABLE keep (a INTEGER);\nTHIS IS NOT SQL;")
    with pytest.raises(sqlite3.OperationalError):
        db.migrate(dbfile)

    con = sqlite3.connect(dbfile)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    applied = {r[0] for r in con.execute("SELECT name FROM schema_migrations")}
    con.close()
    assert "keep" not in tables  # rolled back entirely — not half-applied
    assert "001_x.sql" not in applied  # and not marked done, so it re-runs

    # Fix the file; the retry applies cleanly and records it.
    _write_migration(mdir, "001_x.sql", "CREATE TABLE keep (a INTEGER);")
    db.migrate(dbfile)
    con = sqlite3.connect(dbfile)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    applied = {r[0] for r in con.execute("SELECT name FROM schema_migrations")}
    con.close()
    assert "keep" in tables and "001_x.sql" in applied


def test_self_transactional_migration_still_applies(tmp_path, monkeypatch):
    mdir = tmp_path / "migrations"
    mdir.mkdir()
    monkeypatch.setattr(db, "MIGRATIONS_DIR", mdir)
    dbfile = tmp_path / "t.db"
    # A migration that manages its own BEGIN/COMMIT (like 031) must not be double-wrapped.
    _write_migration(mdir, "001_backfill.sql", "BEGIN;\nCREATE TABLE tb (a INTEGER);\nCOMMIT;")
    db.migrate(dbfile)
    con = sqlite3.connect(dbfile)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    applied = {r[0] for r in con.execute("SELECT name FROM schema_migrations")}
    con.close()
    assert "tb" in tables and "001_backfill.sql" in applied


# ───────────────────────────── #5 failed-cancel visibility ─────────────────────────────


def _configure_saas(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SECRET_KEY", "cleanup-secret")
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "op-pw")
    saas._MIGRATED_TENANT_DBS.clear()
    saas.migrate_control()


def _operator_request(path):
    cookie = (
        f"{security.ADMIN_COOKIE}="
        f"{security.sign(f'operator:{security._pw_fp(config.ADMIN_PASSWORD)}')}"
    )
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "query_string": b"",
            "headers": [
                (b"host", b"mise.test"),
                (b"accept", b"text/html"),
                (b"cookie", cookie.encode()),
            ],
            "scheme": "https",
            "server": ("mise.test", 443),
            "client": ("127.0.0.1", 50000),
        }
    )


def test_failed_stripe_cancel_surfaces_for_manual_follow_up(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    t = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    with saas.control_connect() as con:
        con.execute("UPDATE tenants SET stripe_subscription_id='sub_live' WHERE id=?", (t["id"],))
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_x")

    class _Sub:
        @staticmethod
        def cancel(*a, **kw):
            raise RuntimeError("stripe 503")

    monkeypatch.setattr(saas, "_stripe", lambda: type("S", (), {"Subscription": _Sub}))
    pings = []
    from app import alerts

    monkeypatch.setattr(alerts, "notify", lambda text: pings.append(text))

    saas.delete_tenant_studio(saas.tenant_by_id(t["id"]))

    # The tombstone committed, but the failed cancel is now visible + pinged.
    failures = saas.departed_needs_cancel()
    assert len(failures) == 1 and failures[0]["studio_name"] == "Alpha Studio"
    assert failures[0]["subscription_id"] == "sub_live"
    assert pings and "sub_live" in pings[0] and "reconciliation" in pings[0]

    # The operator cancels in Stripe by hand, then dismisses the reminder.
    resp = asyncio.run(
        saas.operator_cancel_resolved(
            _operator_request("x"),
            t["id"],
            subscription_id="sub_live",
        )
    )
    assert resp.status_code == 303
    assert saas.departed_needs_cancel() == []


def test_successful_cancel_leaves_no_follow_up(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    t = saas.create_tenant("beta", "Beta Studio", "beta@example.com", "secret123")
    with saas.control_connect() as con:
        con.execute("UPDATE tenants SET stripe_subscription_id='sub_ok' WHERE id=?", (t["id"],))
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(
        saas,
        "_stripe",
        lambda: type(
            "S", (), {"Subscription": type("X", (), {"cancel": staticmethod(lambda *a, **k: None)})}
        ),
    )
    saas.delete_tenant_studio(saas.tenant_by_id(t["id"]))
    assert saas.departed_needs_cancel() == []


def test_process_crash_before_stripe_result_leaves_durable_cancel_outbox(
    tmp_path,
    monkeypatch,
):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant(
        "gamma",
        "Gamma Studio",
        "gamma@example.com",
        "secret123",
    )
    with saas.control_connect() as con:
        con.execute(
            "UPDATE tenants SET stripe_subscription_id='sub_crash' WHERE id=?",
            (tenant["id"],),
        )
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_x")

    class SimulatedProcessDeath(BaseException):
        pass

    class _Sub:
        @staticmethod
        def cancel(*_args, **_kwargs):
            raise SimulatedProcessDeath

    monkeypatch.setattr(
        saas,
        "_stripe",
        lambda: type("S", (), {"Subscription": _Sub}),
    )

    with pytest.raises(SimulatedProcessDeath):
        saas.delete_tenant_studio(saas.tenant_by_id(tenant["id"]))

    deleted = saas.tenant_by_id(tenant["id"])
    assert deleted["deleted_at"] is not None
    assert deleted["cancel_failed_at"] is not None
    pending = saas.departed_needs_cancel()
    assert [row["subscription_id"] for row in pending] == ["sub_crash"]
    assert pending[0]["attempted_at"] is not None

    calls: list[str] = []
    monkeypatch.setattr(
        saas,
        "_stripe",
        lambda: type(
            "S",
            (),
            {
                "Subscription": type(
                    "Sub",
                    (),
                    {
                        "cancel": staticmethod(
                            lambda subscription_id, **_kwargs: calls.append(subscription_id)
                        )
                    },
                )
            },
        ),
    )
    saas.delete_tenant_studio(deleted)
    assert calls == []
    assert saas.tenant_by_id(tenant["id"])["storage_parked_at"] is not None
