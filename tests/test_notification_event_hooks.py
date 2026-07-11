"""Notification events commit atomically with their source transitions."""

import datetime as dt

import pytest

from app import config, db, gcal, push_notifications, scheduling
from app.public import docs, pay

pytestmark = pytest.mark.unit


@pytest.fixture
def notification_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "mise.db")
    monkeypatch.setattr(config, "SECRET_KEY", "notification-hook-secret")
    db.migrate()


def _event_type():
    event_id = db.run(
        """INSERT INTO event_types
           (slug,name,duration_min,min_notice_hours,booking_window_days)
           VALUES ('portrait','Portrait',60,0,3650)"""
    )
    return db.one("SELECT * FROM event_types WHERE id=?", (event_id,))


def _proposal():
    client_id = db.run("INSERT INTO clients (name,email) VALUES ('Client','c@example.test')")
    project_id = db.run(
        "INSERT INTO projects (client_id,title) VALUES (?, 'Portrait project')",
        (client_id,),
    )
    proposal_id = db.run(
        """INSERT INTO proposals (project_id,slug,title,status)
           VALUES (?,'proposal-link','Portrait proposal','sent')""",
        (project_id,),
    )
    return proposal_id, project_id


def _invoice():
    client_id = db.run("INSERT INTO clients (name,email) VALUES ('Client','c@example.test')")
    project_id = db.run(
        "INSERT INTO projects (client_id,title) VALUES (?, 'Invoice project')",
        (client_id,),
    )
    invoice_id = db.run(
        """INSERT INTO invoices
           (project_id,slug,title,total_cents,deposit_cents,status)
           VALUES (?,'invoice-link','Invoice',10000,0,'sent')""",
        (project_id,),
    )
    return invoice_id, project_id


def test_new_booking_intake_and_event_share_one_transaction(notification_db, monkeypatch):
    event_type = _event_type()
    start = dt.datetime(2026, 8, 3, 15, 0, tzinfo=dt.UTC)
    monkeypatch.setattr(scheduling, "_slots_utc", lambda *args, **kwargs: [start])

    def fail_event(*args, **kwargs):
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr(push_notifications, "enqueue_owner_event_tx", fail_event)
    with pytest.raises(RuntimeError, match="outbox unavailable"):
        scheduling.book(
            event_type,
            scheduling._fmt_utc(start),
            "Client",
            "client@example.test",
            "",
            "",
            "America/New_York",
            venue_address="123 Main",
        )
    assert db.one("SELECT COUNT(*) AS n FROM bookings")["n"] == 0

    captured = {}

    def capture_event(con, **kwargs):
        captured.update(kwargs)
        assert (
            con.execute("SELECT venue_address FROM bookings ORDER BY id DESC LIMIT 1").fetchone()[
                "venue_address"
            ]
            == "123 Main"
        )
        return []

    monkeypatch.setattr(push_notifications, "enqueue_owner_event_tx", capture_event)
    booking_id, _ = scheduling.book(
        event_type,
        scheduling._fmt_utc(start),
        "Client",
        "client@example.test",
        "",
        "",
        "America/New_York",
        venue_address="123 Main",
    )
    assert captured == {
        "dedupe_key": f"booking.confirmed:{booking_id}",
        "category": "new_bookings",
        "route": f"/app/bookings/{booking_id}",
        "title": push_notifications.alert_copy("new_bookings")[0],
        "body": push_notifications.alert_copy("new_bookings")[1],
    }


def test_public_booking_change_rolls_back_when_outbox_fails(notification_db, monkeypatch):
    event_type = _event_type()
    booking_id = db.run(
        """INSERT INTO bookings
           (token,event_type_id,name,email,start_utc,end_utc,tz)
           VALUES ('manage-link',?,'Client','client@example.test',
                   '2026-08-03 15:00:00','2026-08-03 16:00:00','UTC')""",
        (event_type["id"],),
    )
    monkeypatch.setattr(
        push_notifications,
        "enqueue_owner_event_tx",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("outbox unavailable")),
    )

    with pytest.raises(RuntimeError, match="outbox unavailable"):
        scheduling.cancel("manage-link", "Client request")

    booking = db.one("SELECT status,cancel_reason FROM bookings WHERE id=?", (booking_id,))
    assert booking["status"] == "confirmed"
    assert booking["cancel_reason"] == ""


def test_public_reschedule_and_event_commit_or_roll_back_as_one_transition(
    notification_db, monkeypatch
):
    event_type = _event_type()
    old_id = db.run(
        """INSERT INTO bookings
           (token,event_type_id,name,email,start_utc,end_utc,tz)
           VALUES ('reschedule-link',?,'Client','client@example.test',
                   '2026-08-03 15:00:00','2026-08-03 16:00:00','UTC')""",
        (event_type["id"],),
    )
    replacement_start = dt.datetime(2026, 8, 4, 15, 0, tzinfo=dt.UTC)
    monkeypatch.setattr(scheduling, "_slots_utc", lambda *args, **kwargs: [replacement_start])
    monkeypatch.setattr(
        push_notifications,
        "enqueue_owner_event_tx",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("outbox unavailable")),
    )

    with pytest.raises(RuntimeError, match="outbox unavailable"):
        scheduling.book(
            event_type,
            scheduling._fmt_utc(replacement_start),
            "Client",
            "client@example.test",
            "",
            "",
            "UTC",
            exclude_id=old_id,
        )
    assert db.one("SELECT COUNT(*) AS n FROM bookings")["n"] == 1
    assert db.one("SELECT status FROM bookings WHERE id=?", (old_id,))["status"] == "confirmed"

    captured = {}

    def capture_event(con, **kwargs):
        captured.update(kwargs)
        source = con.execute(
            "SELECT status,cancel_reason FROM bookings WHERE id=?", (old_id,)
        ).fetchone()
        replacement = con.execute(
            "SELECT id,status,reschedule_of FROM bookings WHERE reschedule_of=?", (old_id,)
        ).fetchone()
        assert tuple(source) == ("cancelled", "Rescheduled")
        assert replacement["status"] == "confirmed"
        return []

    monkeypatch.setattr(push_notifications, "enqueue_owner_event_tx", capture_event)
    replacement_id, _ = scheduling.book(
        event_type,
        scheduling._fmt_utc(replacement_start),
        "Client",
        "client@example.test",
        "",
        "",
        "UTC",
        exclude_id=old_id,
    )
    assert captured == {
        "dedupe_key": f"booking.rescheduled:{old_id}:{replacement_id}",
        "category": "booking_changes",
        "route": f"/app/bookings/{replacement_id}",
        "title": push_notifications.alert_copy("booking_changes")[0],
        "body": push_notifications.alert_copy("booking_changes")[1],
    }


def test_public_booking_revalidation_applies_fresh_google_busy_intervals(
    notification_db, monkeypatch
):
    event_type = _event_type()
    start = dt.datetime(2026, 8, 3, 15, 0, tzinfo=dt.UTC)
    end = start + dt.timedelta(minutes=60)
    observed = {}
    monkeypatch.setattr(gcal, "free_busy", lambda *args: [(start, end)])

    def slots(con, event, day, reference, busy, exclude_id=None):
        observed["busy"] = busy
        return [] if busy else [start]

    monkeypatch.setattr(scheduling, "_slots_utc", slots)

    with pytest.raises(scheduling.SlotTaken):
        scheduling.book(
            event_type,
            scheduling._fmt_utc(start),
            "Client",
            "client@example.test",
            "",
            "",
            "UTC",
        )

    assert observed["busy"] == [(start, end)]
    assert db.one("SELECT COUNT(*) AS n FROM bookings")["n"] == 0


def test_public_proposal_decision_rolls_back_when_outbox_fails(notification_db, monkeypatch):
    proposal_id, _ = _proposal()
    monkeypatch.setattr(
        push_notifications,
        "enqueue_owner_event_tx",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("outbox unavailable")),
    )

    with pytest.raises(RuntimeError, match="outbox unavailable"):
        docs._apply_proposal_decision("proposal-link", "accepted")

    assert db.one("SELECT status FROM proposals WHERE id=?", (proposal_id,))["status"] == "sent"


def test_payment_transition_rolls_back_when_outbox_fails(notification_db, monkeypatch):
    invoice_id, _ = _invoice()
    event = {"id": "evt_atomic", "data": {"object": {}}}
    session = {
        "id": "cs_atomic",
        "metadata": {"invoice_id": str(invoice_id), "kind": "full"},
        "amount_total": 10000,
    }
    monkeypatch.setattr(
        push_notifications,
        "enqueue_owner_event_tx",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("outbox unavailable")),
    )

    with pytest.raises(RuntimeError, match="outbox unavailable"):
        pay._record_paid_session(event, session)

    assert db.one("SELECT COUNT(*) AS n FROM payments")["n"] == 0
    assert db.one("SELECT status FROM invoices WHERE id=?", (invoice_id,))["status"] == "sent"


def test_payment_outbox_integrity_error_is_not_misclassified_as_duplicate(
    notification_db,
    monkeypatch,
):
    invoice_id, _ = _invoice()
    event = {"id": "evt_outbox_integrity", "data": {"object": {}}}
    session = {
        "id": "cs_outbox_integrity",
        "metadata": {"invoice_id": str(invoice_id), "kind": "full"},
        "amount_total": 10000,
    }
    monkeypatch.setattr(
        push_notifications,
        "enqueue_owner_event_tx",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            db.sqlite3.IntegrityError("outbox constraint")
        ),
    )

    with pytest.raises(db.sqlite3.IntegrityError, match="outbox constraint"):
        pay._record_paid_session(event, session)

    assert db.one("SELECT COUNT(*) AS n FROM payments")["n"] == 0
    assert db.one("SELECT status FROM invoices WHERE id=?", (invoice_id,))["status"] == "sent"


def test_stripe_redelivery_repairs_post_commit_workflow_after_crash(
    notification_db,
    monkeypatch,
):
    invoice_id, _ = _invoice()
    event = {"id": "evt_workflow_repair", "data": {"object": {}}}
    session = {
        "id": "cs_workflow_repair",
        "metadata": {"invoice_id": str(invoice_id), "kind": "full"},
        "amount_total": 10000,
    }
    record_calls = 0

    def record_then_crash(*args, **kwargs):
        nonlocal record_calls
        record_calls += 1
        if record_calls == 1:
            raise RuntimeError("crash after payment commit")

    monkeypatch.setattr(pay.workflows, "record_project_event", record_then_crash)
    monkeypatch.setattr(pay.workflows, "fire_workflow", lambda *args, **kwargs: None)
    monkeypatch.setattr(pay.jobs, "enqueue", lambda *args, **kwargs: None)

    with pytest.raises(RuntimeError, match="crash after payment commit"):
        pay._record_paid_session(event, session)
    repaired = pay._record_paid_session(event, session)

    assert repaired == {"ok": True, "duplicate": True}
    assert record_calls == 2
    assert db.one("SELECT COUNT(*) AS n FROM payments")["n"] == 1
    assert db.one("SELECT status FROM invoices WHERE id=?", (invoice_id,))["status"] == "paid"
    assert db.one("SELECT COUNT(*) AS n FROM mobile_notification_events")["n"] == 1


def test_double_charge_redelivery_does_not_invent_a_settlement_workflow(
    notification_db,
    monkeypatch,
):
    invoice_id, _ = _invoice()
    first_event = {"id": "evt_first_charge", "data": {"object": {}}}
    first_session = {
        "id": "cs_first_charge",
        "metadata": {"invoice_id": str(invoice_id), "kind": "full"},
        "amount_total": 10000,
    }
    second_event = {"id": "evt_second_charge", "data": {"object": {}}}
    second_session = {
        "id": "cs_second_charge",
        "metadata": {"invoice_id": str(invoice_id), "kind": "full"},
        "amount_total": 10000,
    }
    workflow_events: list[str] = []
    monkeypatch.setattr(
        pay.workflows,
        "record_project_event",
        lambda *args, **kwargs: workflow_events.append(kwargs["dedupe_key"]),
    )
    monkeypatch.setattr(pay.workflows, "fire_workflow", lambda *args, **kwargs: None)
    monkeypatch.setattr(pay.jobs, "enqueue", lambda *args, **kwargs: None)
    monkeypatch.setattr(pay.alerts, "security_alert", lambda message: None)

    assert pay._record_paid_session(first_event, first_session) == {"ok": True}
    assert pay._record_paid_session(second_event, second_session) == {
        "ok": True,
        "duplicate_charge": True,
    }
    assert pay._record_paid_session(second_event, second_session) == {
        "ok": True,
        "duplicate": True,
    }

    assert workflow_events == ["invoice_paid:evt_first_charge"]
    assert db.one("SELECT COUNT(*) AS n FROM payments")["n"] == 2
    assert db.one("SELECT COUNT(*) AS n FROM mobile_notification_events")["n"] == 1
