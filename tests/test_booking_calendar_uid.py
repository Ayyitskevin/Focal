"""Calendar identity stays stable for old bookings and isolated across tenants."""

import shutil
import sqlite3

import pytest

from app import db, ics

pytestmark = pytest.mark.unit


def test_new_booking_uid_is_tenant_scoped_and_opaque(monkeypatch):
    token = "same-tenant-local-token"
    monkeypatch.setattr(ics.urls, "public_base_url", lambda: "https://alpha.mise.test")
    alpha = ics.new_uid(token)
    monkeypatch.setattr(ics.urls, "public_base_url", lambda: "https://beta.mise.test")
    beta = ics.new_uid(token)

    assert alpha != beta
    assert token not in alpha
    assert "alpha" not in alpha
    assert ics.new_uid(token) == beta


def test_persisted_uid_wins_and_legacy_fallback_is_exact():
    assert ics.uid_for(7, "persisted@example.test") == "persisted@example.test"
    assert ics.uid_for(7) == "mise-booking-7@kleephotography.com"


def test_migration_083_upgrades_legacy_tenant_calendar_uids(tmp_path, monkeypatch):
    tenant_db = tmp_path / "tenants" / "legacy.db"
    staged_migrations = tmp_path / "migrations"
    staged_migrations.mkdir()
    migration_source = db.MIGRATIONS_DIR
    for migration in migration_source.glob("*.sql"):
        if migration.name < "083_":
            shutil.copy2(migration, staged_migrations / migration.name)
    monkeypatch.setattr(db, "MIGRATIONS_DIR", staged_migrations)

    db.migrate(tenant_db)
    con = db.connect(tenant_db)
    try:
        event_id = con.execute(
            "INSERT INTO event_types (slug,name) VALUES (?,?)",
            ("legacy-calendar", "Legacy calendar"),
        ).lastrowid
        legacy_id = con.execute(
            """INSERT INTO bookings
               (token,event_type_id,name,email,start_utc,end_utc,tz)
               VALUES (?,?,?,?,?,?,?)""",
            (
                "legacy-booking",
                event_id,
                "Legacy Client",
                "legacy@example.test",
                "2026-08-01 14:00:00",
                "2026-08-01 15:00:00",
                "UTC",
            ),
        ).lastrowid
        con.commit()
        assert "calendar_uid" not in {
            row["name"] for row in con.execute("PRAGMA table_info(bookings)")
        }
    finally:
        con.close()

    shutil.copy2(
        migration_source / "083_booking_workflow_effects.sql",
        staged_migrations / "083_booking_workflow_effects.sql",
    )
    db.migrate(tenant_db)

    expected_uid = f"mise-booking-{legacy_id}@kleephotography.com"
    con = db.connect(tenant_db)
    try:
        row = con.execute("SELECT calendar_uid FROM bookings WHERE id=?", (legacy_id,)).fetchone()
        assert row["calendar_uid"] == expected_uid
        index = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
            ("idx_bookings_calendar_uid",),
        ).fetchone()
        assert "UNIQUE INDEX" in index["sql"]
        assert "WHERE calendar_uid IS NOT NULL" in index["sql"]

        with pytest.raises(sqlite3.IntegrityError, match="bookings.calendar_uid"):
            con.execute(
                """INSERT INTO bookings
                   (token,event_type_id,name,email,start_utc,end_utc,tz,calendar_uid)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    "duplicate-calendar-uid",
                    event_id,
                    "Duplicate Client",
                    "duplicate@example.test",
                    "2026-08-02 14:00:00",
                    "2026-08-02 15:00:00",
                    "UTC",
                    expected_uid,
                ),
            )
        con.rollback()
        con.execute(
            """INSERT INTO bookings
               (token,event_type_id,name,email,start_utc,end_utc,tz,calendar_uid)
               VALUES (?,?,?,?,?,?,?,NULL)""",
            (
                "nullable-calendar-uid-a",
                event_id,
                "Nullable Client A",
                "nullable-a@example.test",
                "2026-08-03 14:00:00",
                "2026-08-03 15:00:00",
                "UTC",
            ),
        )
        con.execute(
            """INSERT INTO bookings
               (token,event_type_id,name,email,start_utc,end_utc,tz,calendar_uid)
               VALUES (?,?,?,?,?,?,?,NULL)""",
            (
                "nullable-calendar-uid-b",
                event_id,
                "Nullable Client B",
                "nullable-b@example.test",
                "2026-08-04 14:00:00",
                "2026-08-04 15:00:00",
                "UTC",
            ),
        )
        con.commit()
        applied_before = con.execute(
            "SELECT applied_at FROM schema_migrations WHERE name=?",
            ("083_booking_workflow_effects.sql",),
        ).fetchone()["applied_at"]
    finally:
        con.close()

    db.migrate(tenant_db)
    con = db.connect(tenant_db)
    try:
        applied = con.execute(
            "SELECT applied_at FROM schema_migrations WHERE name=?",
            ("083_booking_workflow_effects.sql",),
        ).fetchall()
        assert [row["applied_at"] for row in applied] == [applied_before]
    finally:
        con.close()
