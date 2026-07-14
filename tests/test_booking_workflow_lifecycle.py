"""Lifecycle guards for durable booking-reschedule delivery.

These tests exercise the seam between a booking's mutable lifecycle and its
at-least-once reschedule effects. Once a replacement is itself cancelled or
rescheduled, stale effects must never recreate provider state for that old
replacement. The old source CANCEL is the sole exception: retiring the original
calendar UID remains correct and safe to deliver.
"""

import datetime as dt
import threading
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import (
    booking_notify,
    booking_workflow,
    config,
    db,
    features,
    gcal,
    mailer,
    notion_sync,
    ratelimit,
    scheduling,
)
from app.main import app

pytestmark = pytest.mark.unit

_NOW = dt.datetime(2026, 7, 13, 12, 0, tzinfo=dt.UTC)
_SOURCE_START = "2026-07-15 10:00:00"
_REPLACEMENT_START = "2026-07-16 11:00:00"
_CHAINED_START = "2026-07-17 12:00:00"


@pytest.fixture
def lifecycle_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "mise.db")
    monkeypatch.setattr(config, "SECRET_KEY", "booking-lifecycle-secret")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "owner-password")
    monkeypatch.setattr(config, "BASE_URL", "https://studio.test")
    monkeypatch.setattr(config, "TIMEZONE", "UTC")
    monkeypatch.setattr(config, "SITE_NAME", "Lifecycle Studio")
    monkeypatch.setattr(config, "GMAIL_USER", "studio@example.test")
    monkeypatch.setattr(config, "GMAIL_APP_PASSWORD", "test-app-password")
    monkeypatch.setattr(config, "BOOKING_WORKFLOW_ENABLED", True)
    monkeypatch.setattr(scheduling, "now_utc", lambda: _NOW)
    ratelimit._hits.clear()
    db.migrate()
    yield
    ratelimit._hits.clear()


def _plus_hour(value: str) -> str:
    parsed = dt.datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    return (parsed + dt.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")


def _seed_event() -> int:
    event_id = db.run(
        """INSERT INTO event_types
           (slug,name,description,duration_min,location,color,buffer_before_min,
            buffer_after_min,min_notice_hours,max_per_day,booking_window_days,
            slot_step_min,active,position,creates_notion_session)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,1,1)""",
        (
            f"lifecycle-{uuid4().hex}",
            "Lifecycle session",
            "A representative session.",
            60,
            "Studio",
            "#123ABC",
            0,
            0,
            0,
            0,
            30,
            60,
        ),
    )
    for weekday in range(7):
        db.run(
            """INSERT INTO availability_rules
               (event_type_id,weekday,start_min,end_min) VALUES (?,?,0,1440)""",
            (event_id, weekday),
        )
    return event_id


def _seed_pending_pair() -> dict:
    event_id = _seed_event()
    source_token = f"source-{uuid4().hex}"
    replacement_token = f"replacement-{uuid4().hex}"
    source_id = db.run(
        """INSERT INTO bookings
           (token,event_type_id,name,email,phone,notes,start_utc,end_utc,tz,status,
            cancel_reason,cancelled_at,calendar_uid)
           VALUES (?,?,?,?,?,?,?,?,?,'cancelled',?,datetime('now'),?)""",
        (
            source_token,
            event_id,
            "Alex Rivera",
            "alex@example.test",
            "555-0142",
            "Lifecycle test booking.",
            _SOURCE_START,
            _plus_hour(_SOURCE_START),
            "UTC",
            "Rescheduled",
            f"source-{uuid4().hex}@calendar.test",
        ),
    )
    replacement_id = db.run(
        """INSERT INTO bookings
           (token,event_type_id,name,email,phone,notes,start_utc,end_utc,tz,status,
            reschedule_of,calendar_uid)
           VALUES (?,?,?,?,?,?,?,?,?,'confirmed',?,?)""",
        (
            replacement_token,
            event_id,
            "Alex Rivera",
            "alex@example.test",
            "555-0142",
            "Lifecycle test booking.",
            _REPLACEMENT_START,
            _plus_hour(_REPLACEMENT_START),
            "UTC",
            source_id,
            f"replacement-{uuid4().hex}@calendar.test",
        ),
    )
    workflow_id = str(uuid4())
    with db.tx() as con:
        booking_workflow.enqueue_reschedule(
            con,
            source_booking_id=source_id,
            replacement_booking_id=replacement_id,
            workflow_id=workflow_id,
        )
    return {
        "event_id": event_id,
        "source_id": source_id,
        "source_token": source_token,
        "replacement_id": replacement_id,
        "replacement_token": replacement_token,
        "workflow_id": workflow_id,
    }


def _effect_rows(workflow_id: str) -> dict[str, dict]:
    return {
        row["effect_kind"]: dict(row)
        for row in db.all_(
            """SELECT * FROM booking_workflow_effects
                WHERE workflow_id=? ORDER BY sequence_no""",
            (workflow_id,),
        )
    }


def _install_delivery_spies(monkeypatch) -> tuple[list[tuple], list[tuple]]:
    sent: list[tuple] = []
    provider_calls: list[tuple] = []

    def capture_mail(*args, **kwargs):
        sent.append((args, kwargs))

    monkeypatch.setattr(mailer, "send", capture_mail)
    monkeypatch.setattr(features, "notion_bookings_enabled", lambda: True)
    monkeypatch.setattr(features, "notion_sessions_enabled", lambda: True)
    monkeypatch.setattr(gcal, "configured", lambda: True)
    monkeypatch.setattr(gcal, "is_connected", lambda: True)
    monkeypatch.setattr(
        notion_sync,
        "reschedule_booking",
        lambda *args: provider_calls.append(("notion_booking", *args)) or "booking-page",
    )
    monkeypatch.setattr(
        notion_sync,
        "reschedule_session",
        lambda *args: provider_calls.append(("notion_session", *args)) or "session-page",
    )
    monkeypatch.setattr(
        gcal,
        "on_booking_rescheduled",
        lambda *args, **kwargs: (
            provider_calls.append(("google", *args, kwargs)) or "calendar-event"
        ),
    )
    return sent, provider_calls


def _assert_only_source_cancel_survives(workflow_id: str) -> None:
    effects = _effect_rows(workflow_id)
    assert effects["client_cancel_ics"]["status"] == "pending"
    for kind, _sequence in booking_workflow.EFFECT_KINDS:
        if kind == "client_cancel_ics":
            continue
        effect = effects[kind]
        assert effect["status"] == "skipped"
        assert effect["error_class"] == "WorkflowSuperseded"
        assert effect["error_code"] == "replacement_superseded"
        assert effect["completed_at"] is not None


def _assert_cancel_mail_does_not_promise_replacement(sent: list[tuple]) -> None:
    assert len(sent) == 1
    args, kwargs = sent[0]
    assert "Previous booking time cancelled" in args[1]
    assert "follows separately" not in args[2]
    assert "no new invitation will follow" in args[2]
    assert kwargs["ics"]["method"] == "CANCEL"
    assert "METHOD:CANCEL" in kwargs["ics"]["content"]


def test_cancelled_replacement_supersedes_every_effect_except_old_cancel(
    lifecycle_db,
    monkeypatch,
):
    pair = _seed_pending_pair()
    sent, provider_calls = _install_delivery_spies(monkeypatch)

    assert scheduling.cancel(pair["replacement_token"], "Client cancelled replacement") is True

    replacement = db.one("SELECT * FROM bookings WHERE id=?", (pair["replacement_id"],))
    assert replacement["status"] == "cancelled"
    assert replacement["cancel_reason"] == "Client cancelled replacement"
    _assert_only_source_cancel_survives(pair["workflow_id"])

    assert booking_workflow.dispatch_workflow(pair["workflow_id"]) == 1

    effects = _effect_rows(pair["workflow_id"])
    assert effects["client_cancel_ics"]["status"] == "succeeded"
    assert all(effect["status"] in {"succeeded", "skipped"} for effect in effects.values())
    _assert_cancel_mail_does_not_promise_replacement(sent)
    assert provider_calls == []
    assert (
        db.one("SELECT status FROM bookings WHERE id=?", (pair["replacement_id"],))["status"]
        == "cancelled"
    )


def test_chained_reschedule_skips_stale_replacement_effects_and_keeps_old_cancel(
    lifecycle_db,
    monkeypatch,
):
    pair = _seed_pending_pair()
    sent, provider_calls = _install_delivery_spies(monkeypatch)
    event = scheduling.event_by_slug(
        db.one("SELECT slug FROM event_types WHERE id=?", (pair["event_id"],))["slug"]
    )

    chained_id, _chained_token = scheduling.reschedule(
        event,
        _CHAINED_START,
        "Alex Rivera",
        "alex@example.test",
        "555-0142",
        "Lifecycle test booking.",
        "UTC",
        source_booking_id=pair["replacement_id"],
    )

    replacement = db.one("SELECT * FROM bookings WHERE id=?", (pair["replacement_id"],))
    chained = db.one("SELECT * FROM bookings WHERE id=?", (chained_id,))
    assert replacement["status"] == "cancelled"
    assert chained["status"] == "confirmed"
    assert chained["reschedule_of"] == pair["replacement_id"]
    _assert_only_source_cancel_survives(pair["workflow_id"])
    assert [
        (row["effect_kind"], row["replacement_booking_id"])
        for row in db.all_(
            """SELECT effect_kind, replacement_booking_id
                 FROM booking_workflow_effects
                WHERE status IN ('pending','retry','running')
                ORDER BY sequence_no"""
        )
    ] == [("client_cancel_ics", pair["replacement_id"])]

    assert booking_workflow.dispatch_workflow(pair["workflow_id"]) == 1
    _assert_cancel_mail_does_not_promise_replacement(sent)
    assert provider_calls == []
    assert db.one("SELECT status FROM bookings WHERE id=?", (chained_id,))["status"] == "confirmed"


def test_running_source_cancel_copy_stays_true_when_replacement_is_superseded(
    lifecycle_db,
    monkeypatch,
):
    pair = _seed_pending_pair()
    sent: list[tuple] = []
    send_started = threading.Event()
    allow_send = threading.Event()
    outcome: dict[str, object] = {}

    def pause_at_send(*args, **kwargs):
        sent.append((args, kwargs))
        send_started.set()
        if not allow_send.wait(timeout=5):
            raise TimeoutError("test did not release the paused calendar cancellation")

    monkeypatch.setattr(mailer, "send", pause_at_send)

    def dispatch() -> None:
        try:
            outcome["attempted"] = booking_workflow.dispatch_workflow(pair["workflow_id"])
        except Exception as exc:  # pragma: no cover - surfaced by the main test thread
            outcome["error"] = exc

    worker = threading.Thread(target=dispatch, daemon=True)
    worker.start()
    assert send_started.wait(timeout=5), "calendar cancellation never reached SMTP send"

    try:
        before = _effect_rows(pair["workflow_id"])
        assert before["client_cancel_ics"]["status"] == "running"
        assert all(
            effect["status"] == "pending"
            for kind, effect in before.items()
            if kind != "client_cancel_ics"
        )

        assert scheduling.cancel(
            pair["replacement_token"],
            "Cancelled while the prior-time notice was sending",
        )
        during = _effect_rows(pair["workflow_id"])
        assert during["client_cancel_ics"]["status"] == "running"
        assert all(
            effect["status"] == "skipped" and effect["error_code"] == "replacement_superseded"
            for kind, effect in during.items()
            if kind != "client_cancel_ics"
        )
    finally:
        allow_send.set()

    worker.join(timeout=5)
    assert not worker.is_alive()
    assert "error" not in outcome
    assert outcome["attempted"] == 1

    final = _effect_rows(pair["workflow_id"])
    assert final["client_cancel_ics"]["status"] == "succeeded"
    assert all(
        effect["status"] == "skipped"
        for kind, effect in final.items()
        if kind != "client_cancel_ics"
    )
    assert len(sent) == 1
    body = sent[0][0][2]
    assert "If your replacement remains active" in body
    assert "If it is cancelled or replaced, no new invitation will follow" in body
    assert "Your replacement invitation" not in body


def _mobile_login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/studio/login",
        json={
            "email": None,
            "password": "owner-password",
            "device": {
                "installation_id": str(uuid4()).upper(),
                "name": "Owner iPhone",
                "platform": "ios",
                "app_version": "1.0",
            },
        },
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _workflow_snapshot(workflow_id: str) -> list[dict]:
    return [
        dict(row)
        for row in db.all_(
            """SELECT id, workflow_id, source_booking_id, replacement_booking_id,
                      effect_kind, sequence_no, status, attempts, next_attempt_at,
                      lease_token, lease_expires_at, provider_ref, error_class,
                      error_code, completed_at, created_at, updated_at
                 FROM booking_workflow_effects
                WHERE workflow_id=? ORDER BY sequence_no""",
            (workflow_id,),
        )
    ]


def _booking_snapshot(booking_id: int) -> dict:
    return dict(
        db.one(
            """SELECT id, status, cancel_reason, cancelled_at, start_utc, end_utc,
                      reschedule_of, token
                 FROM bookings WHERE id=?""",
            (booking_id,),
        )
    )


def test_running_effect_blocks_mobile_public_and_admin_cancel_without_side_effects(
    lifecycle_db,
    monkeypatch,
):
    pair = _seed_pending_pair()
    now = booking_workflow.now_ts()
    db.run(
        """UPDATE booking_workflow_effects
              SET status='running', attempts=1, next_attempt_at=NULL,
                  lease_token='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                  lease_expires_at=?, updated_at=?
            WHERE workflow_id=? AND effect_kind='client_request_ics'""",
        (now + 120, now, pair["workflow_id"]),
    )
    notifications: list[tuple] = []
    monkeypatch.setattr(
        booking_notify,
        "cancelled",
        lambda *args, **kwargs: notifications.append((args, kwargs)),
    )
    client = TestClient(app, base_url="https://studio.test")
    headers = _mobile_login(client)
    admin_login = client.post(
        "/admin/login",
        data={"password": "owner-password"},
        follow_redirects=False,
    )
    assert admin_login.status_code == 303
    booking_before = _booking_snapshot(pair["replacement_id"])
    workflow_before = _workflow_snapshot(pair["workflow_id"])
    audit_before = db.one("SELECT COUNT(*) AS n FROM audit_log")["n"]

    mobile = client.post(
        f"/api/v1/bookings/{pair['replacement_id']}/cancel",
        headers=headers,
        follow_redirects=False,
    )
    assert mobile.status_code == 409
    assert mobile.json()["code"] == "booking.workflow_in_progress"
    assert _booking_snapshot(pair["replacement_id"]) == booking_before
    assert _workflow_snapshot(pair["workflow_id"]) == workflow_before

    public = client.post(
        f"/booking/{pair['replacement_token']}/cancel",
        data={"reason": "Client cancellation"},
        follow_redirects=False,
    )
    assert public.status_code == 409
    assert _booking_snapshot(pair["replacement_id"]) == booking_before
    assert _workflow_snapshot(pair["workflow_id"]) == workflow_before

    booking_count = db.one("SELECT COUNT(*) AS n FROM bookings")["n"]
    public_reschedule = client.post(
        f"/booking/{pair['replacement_token']}/reschedule",
        data={"start": _CHAINED_START, "tz": "UTC"},
        follow_redirects=False,
    )
    assert public_reschedule.status_code == 409
    assert db.one("SELECT COUNT(*) AS n FROM bookings")["n"] == booking_count
    assert _booking_snapshot(pair["replacement_id"]) == booking_before
    assert _workflow_snapshot(pair["workflow_id"]) == workflow_before

    admin = client.post(
        f"/admin/scheduling/booking/{pair['replacement_id']}/cancel",
        follow_redirects=False,
    )
    assert admin.status_code == 409
    assert _booking_snapshot(pair["replacement_id"]) == booking_before
    assert _workflow_snapshot(pair["workflow_id"]) == workflow_before
    assert db.one("SELECT COUNT(*) AS n FROM audit_log")["n"] == audit_before
    assert notifications == []
    client.close()


@pytest.mark.parametrize("unavailable_gate", ["disabled", "mailer"])
def test_expired_running_effect_does_not_block_public_cancel_when_worker_unavailable(
    lifecycle_db,
    monkeypatch,
    unavailable_gate,
):
    pair = _seed_pending_pair()
    now = booking_workflow.now_ts()
    db.run(
        """UPDATE booking_workflow_effects
              SET status='running', attempts=1, next_attempt_at=NULL,
                  lease_token='bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
                  lease_expires_at=?, updated_at=?
            WHERE workflow_id=? AND effect_kind='client_request_ics'""",
        (now - 1, now - 1, pair["workflow_id"]),
    )
    if unavailable_gate == "disabled":
        monkeypatch.setattr(config, "BOOKING_WORKFLOW_ENABLED", False)
    else:
        monkeypatch.setattr(mailer, "configured", lambda: False)
    assert booking_workflow.available() is False

    notifications: list[tuple] = []
    monkeypatch.setattr(
        booking_notify,
        "cancelled",
        lambda *args, **kwargs: notifications.append((args, kwargs)),
    )
    client = TestClient(app, base_url="https://studio.test")
    response = client.post(
        f"/booking/{pair['replacement_token']}/cancel",
        data={"reason": "Client cancellation"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    replacement = _booking_snapshot(pair["replacement_id"])
    assert replacement["status"] == "cancelled"
    assert replacement["cancel_reason"] == "Client cancellation"
    _assert_only_source_cancel_survives(pair["workflow_id"])
    expired = _effect_rows(pair["workflow_id"])["client_request_ics"]
    assert expired["attempts"] == 1
    assert expired["lease_token"] is None
    assert expired["lease_expires_at"] is None
    assert len(notifications) == 1
    client.close()
