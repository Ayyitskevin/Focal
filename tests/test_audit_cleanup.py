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


def test_offsite_sync_versions_the_mirror_with_backup_dir(tmp_path, monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(hosted_backup.shutil, "which", lambda _: "/usr/bin/rclone")

    def fake_run(argv, **kw):
        calls.append(argv)

        class R:
            returncode = 0

        return R()

    monkeypatch.setattr(hosted_backup.subprocess, "run", fake_run)
    status = hosted_backup._offsite_sync(
        "b2:mise", tmp_path / "backups", tmp_path / "tenants", "20260703-000000"
    )
    assert status == "synced"
    # Both trees synced, each with a timestamped --backup-dir so overwritten/deleted
    # remote files are MOVED to history, never destroyed.
    subs = {argv[3].rsplit("/", 1)[-1]: argv for argv in calls}
    assert set(subs) == {"backups", "tenants"}
    for sub, argv in subs.items():
        assert "--backup-dir" in argv
        bd = argv[argv.index("--backup-dir") + 1]
        assert bd == f"b2:mise/{sub}-history/20260703-000000"


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
    assert failures[0]["stripe_subscription_id"] == "sub_live"
    assert pings and "sub_live" in pings[0] and "still be charging" in pings[0]

    # The operator cancels in Stripe by hand, then dismisses the reminder.
    resp = asyncio.run(saas.operator_cancel_resolved(_operator_request("x"), t["id"]))
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
