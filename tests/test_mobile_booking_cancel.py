"""Native owner booking cancel — /api/v1 M4a mutation (queue S6b).

Contract: an owner with studio:write cancels a confirmed booking by id; the flip is
server-authoritative and idempotent (a repeat call is a no-op returning the
already-cancelled state — no second cancellation email, no second audit row); each
real confirmed→cancelled transition writes one audit_log row and fires exactly one
booking_notify.cancelled; a guest or read-only owner token is refused.
"""

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app import booking_notify, config, db, mobile_auth, ratelimit
from app.main import app

pytestmark = pytest.mark.unit


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "SECRET_KEY", "booking-api-secret")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "owner-password")
    monkeypatch.setattr(config, "BASE_URL", "https://studio.test")
    monkeypatch.setattr(config, "TIMEZONE", "UTC")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "mise.db")
    ratelimit._hits.clear()
    db.migrate()
    client = TestClient(app, base_url="https://studio.test")
    login = client.post(
        "/api/v1/auth/studio/login",
        json={
            "email": None,
            "password": "owner-password",
            "device": {
                "installation_id": "A8A06DC2-2034-4E3B-B07D-0CBFD2455B98",
                "name": "Owner iPhone",
                "platform": "ios",
                "app_version": "1.0",
            },
        },
    )
    token = login.json()["access_token"]
    yield client, {"Authorization": f"Bearer {token}"}
    client.close()
    ratelimit._hits.clear()


@pytest.fixture
def notify_spy(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(booking_notify, "cancelled", lambda *a, **k: calls.append((a, k)))
    return calls


def _make_confirmed_booking(token: str = "bk-rossi") -> int:
    event_id = db.run(
        """INSERT INTO event_types
           (slug,name,description,duration_min,location,color,buffer_before_min,
            buffer_after_min,min_notice_hours,max_per_day,booking_window_days,
            slot_step_min,active,position)
           VALUES ('tasting','Menu tasting','',45,'Studio','#123ABC',0,0,0,0,90,45,1,1)"""
    )
    return db.run(
        """INSERT INTO bookings
           (token,event_type_id,name,email,start_utc,end_utc,tz,status)
           VALUES (?,?, 'Rossi Trattoria','ops@rossi.test','2099-02-01 15:00:00',
                   '2099-02-01 15:45:00','America/New_York','confirmed')""",
        (token, event_id),
    )


def _audit_rows(booking_id: int) -> list:
    return db.all_(
        "SELECT action, actor FROM audit_log WHERE entity_type='booking' AND entity_id=?"
        " ORDER BY id",
        (booking_id,),
    )


def test_cancel_confirmed_booking(owner, notify_spy):
    client, headers = owner
    booking_id = _make_confirmed_booking()

    resp = client.post(f"/api/v1/bookings/{booking_id}/cancel", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == booking_id
    assert body["status"] == "cancelled"
    assert body["cancelled_at"] is not None
    assert "no-store" in resp.headers["cache-control"] or "private" in resp.headers["cache-control"]

    row = db.one("SELECT status, cancel_reason FROM bookings WHERE id=?", (booking_id,))
    assert row["status"] == "cancelled"
    assert row["cancel_reason"] == "Cancelled from the studio app"
    assert [r["action"] for r in _audit_rows(booking_id)] == ["cancel"]
    assert _audit_rows(booking_id)[0]["actor"] == "owner"
    assert len(notify_spy) == 1  # the client cancellation notice fired exactly once


def test_cancel_is_idempotent(owner, notify_spy):
    client, headers = owner
    booking_id = _make_confirmed_booking()

    first = client.post(f"/api/v1/bookings/{booking_id}/cancel", headers=headers)
    second = client.post(f"/api/v1/bookings/{booking_id}/cancel", headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["status"] == "cancelled"
    # A repeat cancel is a no-op: no second audit row, no second notification.
    assert len(_audit_rows(booking_id)) == 1
    assert len(notify_spy) == 1


def test_unknown_booking_is_404(owner, notify_spy):
    client, headers = owner
    resp = client.post("/api/v1/bookings/999999/cancel", headers=headers)
    assert resp.status_code == 404
    assert notify_spy == []


def test_cancel_requires_bearer(owner, notify_spy):
    client, _ = owner
    booking_id = _make_confirmed_booking()
    assert client.post(f"/api/v1/bookings/{booking_id}/cancel").status_code == 401
    assert db.one("SELECT status FROM bookings WHERE id=?", (booking_id,))["status"] == "confirmed"
    assert notify_spy == []


def test_guest_principal_is_refused(owner, notify_spy, monkeypatch):
    client, headers = owner
    booking_id = _make_confirmed_booking()
    guest = mobile_auth.Principal(
        session_id="s",
        tenant_key="self:https://studio.test",
        kind=mobile_auth.PORTAL_GUEST,
        resource_id=1,
        resource_variant=None,
        gallery_visitor_id=None,
        scopes=frozenset({"portal:1:read"}),
        device_name=None,
        device_platform=None,
        device_app_version=None,
        created_at=dt.datetime.now(dt.UTC),
        absolute_expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=1),
    )
    monkeypatch.setattr(mobile_auth, "authenticate_request", lambda *a, **k: guest)
    resp = client.post(f"/api/v1/bookings/{booking_id}/cancel", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["code"] == "auth.insufficient_scope"
    assert db.one("SELECT status FROM bookings WHERE id=?", (booking_id,))["status"] == "confirmed"
    assert notify_spy == []


def test_read_only_owner_cannot_cancel(owner, notify_spy, monkeypatch):
    client, headers = owner
    booking_id = _make_confirmed_booking()
    read_only = mobile_auth.Principal(
        session_id="s",
        tenant_key="self:https://studio.test",
        kind=mobile_auth.STUDIO_OWNER,
        resource_id=None,
        resource_variant=None,
        gallery_visitor_id=None,
        scopes=frozenset({"studio:read"}),  # no studio:write
        device_name=None,
        device_platform=None,
        device_app_version=None,
        created_at=dt.datetime.now(dt.UTC),
        absolute_expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=1),
    )
    monkeypatch.setattr(mobile_auth, "authenticate_request", lambda *a, **k: read_only)
    resp = client.post(f"/api/v1/bookings/{booking_id}/cancel", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["code"] == "auth.insufficient_scope"
    assert db.one("SELECT status FROM bookings WHERE id=?", (booking_id,))["status"] == "confirmed"
    assert notify_spy == []
