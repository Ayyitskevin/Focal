"""Milestone 4B scheduling and proposal decision contracts."""

import datetime as dt
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app import (
    audit,
    booking_notify,
    config,
    db,
    jobs,
    mobile_policy_mutation_api,
    ratelimit,
    scheduling,
    workflows,
)
from app.main import app

pytestmark = pytest.mark.unit
_NOW = dt.datetime(2026, 7, 10, 12, tzinfo=dt.UTC)


def _device(name: str) -> dict:
    return {
        "installation_id": str(uuid.uuid4()),
        "name": name,
        "platform": "ios",
        "app_version": "1.0",
    }


@pytest.fixture
def policy(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "SECRET_KEY", "policy-secret")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "owner-password")
    monkeypatch.setattr(config, "BASE_URL", "https://studio.test")
    monkeypatch.setattr(config, "TIMEZONE", "UTC")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "mise.db")
    monkeypatch.setattr(scheduling, "now_utc", lambda: _NOW)
    monkeypatch.setattr(mobile_policy_mutation_api.gcal, "free_busy", lambda *args: [])
    ratelimit._hits.clear()
    monkeypatch.setattr(jobs, "kick", lambda _job_id: None)
    db.migrate()

    client_id = db.run(
        "INSERT INTO clients (name,email) VALUES (?,?)",
        ("Avery", "avery@example.test"),
    )
    project_id = db.run(
        "INSERT INTO projects (client_id,title) VALUES (?,?)",
        (client_id, "Campaign"),
    )
    event_id = db.run(
        """INSERT INTO event_types
           (slug,name,duration_min,min_notice_hours,booking_window_days,slot_step_min)
           VALUES ('consult','Consultation',60,0,30,60)"""
    )
    db.run(
        """INSERT INTO date_overrides (event_type_id,day,available,start_min,end_min)
           VALUES (?,?,1,600,780)""",
        (event_id, "2026-07-11"),
    )
    booking_id = db.run(
        """INSERT INTO bookings
           (token,event_type_id,name,email,phone,notes,start_utc,end_utc,tz,
            client_id,project_id,venue_address,dish_count,parking_notes,
            style_refs,onsite_contact)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "owner-booking-token",
            event_id,
            "Avery",
            "avery@example.test",
            "+1 555 0100",
            "Discovery",
            "2026-07-11 10:00:00",
            "2026-07-11 11:00:00",
            "UTC",
            client_id,
            project_id,
            "Studio",
            "4",
            "Rear lot",
            "Warm",
            "Avery",
        ),
    )
    proposal_id = db.run(
        """INSERT INTO proposals
           (project_id,slug,title,intro,line_items,total_cents,status,sent_at)
           VALUES (?,'campaign-proposal','Campaign proposal','Scope','[]',5000,
                   'sent','2026-07-10 10:00:00')""",
        (project_id,),
    )

    client = TestClient(app, base_url="https://studio.test")
    login = client.post(
        "/api/v1/auth/studio/login",
        json={
            "email": None,
            "password": "owner-password",
            "device": _device("Owner iPhone"),
        },
    )
    assert login.status_code == 200
    owner_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    exchange = client.post(
        "/api/v1/client-auth/document/exchange",
        json={
            "kind": "proposal",
            "slug": "campaign-proposal",
            "device": _device("Client iPhone"),
        },
    )
    assert exchange.status_code == 200
    proposal_headers = {"Authorization": f"Bearer {exchange.json()['access_token']}"}
    yield (
        client,
        owner_headers,
        proposal_headers,
        {
            "booking_id": booking_id,
            "event_id": event_id,
            "project_id": project_id,
            "proposal_id": proposal_id,
        },
    )
    client.close()
    ratelimit._hits.clear()


def _command(headers: dict[str, str], *, key: uuid.UUID | None = None, etag: str) -> dict:
    return {
        **headers,
        "Idempotency-Key": str(key or uuid.uuid4()),
        "If-Match": etag,
    }


def _deliver_next_policy_effect() -> int:
    job = db.one(
        """SELECT id FROM jobs
            WHERE kind='mobile_policy_effect' AND status='queued'
            ORDER BY id LIMIT 1"""
    )
    assert job is not None
    jobs._execute(int(job["id"]))
    return int(job["id"])


def test_owner_cancel_is_versioned_replay_safe_and_notifies_once(policy, monkeypatch):
    client, owner_headers, _, ids = policy
    detail = client.get(f"/api/v1/bookings/{ids['booking_id']}", headers=owner_headers)
    assert detail.status_code == 200
    calls = []
    monkeypatch.setattr(
        booking_notify,
        "cancelled",
        lambda booking_id, by_admin=False: calls.append((booking_id, by_admin)),
    )
    key = uuid.uuid4()
    headers = _command(owner_headers, key=key, etag=detail.headers["etag"])
    first = client.post(
        f"/api/v1/bookings/{ids['booking_id']}/cancel",
        headers=headers,
        json={"reason": "Owner conflict"},
    )
    replay = client.post(
        f"/api/v1/bookings/{ids['booking_id']}/cancel",
        headers=headers,
        json={"reason": "Owner conflict"},
    )
    job_id = _deliver_next_policy_effect()
    jobs._execute(job_id)

    assert first.status_code == replay.status_code == 200
    assert first.json()["status"] == "cancelled"
    assert replay.headers["idempotency-replayed"] == "true"
    assert calls == [(ids["booking_id"], True)]
    assert (
        db.one(
            "SELECT COUNT(*) AS n FROM audit_log WHERE entity_type='booking' AND action='cancel'"
        )["n"]
        == 1
    )


def test_owner_reschedule_is_atomic_replay_safe_and_preserves_intake(policy, monkeypatch):
    client, owner_headers, _, ids = policy
    db.run("UPDATE event_types SET max_per_day=1 WHERE id=?", (ids["event_id"],))
    detail = client.get(f"/api/v1/bookings/{ids['booking_id']}", headers=owner_headers)
    slots = client.get(
        f"/api/v1/bookings/{ids['booking_id']}/slots",
        headers=owner_headers,
        params={"day": "2026-07-11", "time_zone": "UTC"},
    )
    assert slots.status_code == 200
    assert [item["start_at"] for item in slots.json()["items"]] == [
        "2026-07-11T10:00:00Z",
        "2026-07-11T11:00:00Z",
        "2026-07-11T12:00:00Z",
    ]

    rescheduled = []
    monkeypatch.setattr(booking_notify, "rescheduled", rescheduled.append)
    key = uuid.uuid4()
    headers = _command(owner_headers, key=key, etag=detail.headers["etag"])
    body = {"start_at": "2026-07-11T11:00:00Z", "time_zone": "UTC"}
    first = client.post(
        f"/api/v1/bookings/{ids['booking_id']}/reschedule",
        headers=headers,
        json=body,
    )
    replay = client.post(
        f"/api/v1/bookings/{ids['booking_id']}/reschedule",
        headers=headers,
        json=body,
    )

    _deliver_next_policy_effect()
    assert first.status_code == replay.status_code == 201
    assert replay.json() == first.json()
    assert replay.headers["idempotency-replayed"] == "true"
    replacement_id = first.json()["id"]
    assert rescheduled == [replacement_id]
    original = db.one("SELECT status,cancel_reason FROM bookings WHERE id=?", (ids["booking_id"],))
    replacement = db.one(
        """SELECT reschedule_of,client_id,project_id,venue_address,dish_count,
                  parking_notes,style_refs,onsite_contact FROM bookings WHERE id=?""",
        (replacement_id,),
    )
    assert tuple(original) == ("cancelled", "Rescheduled")
    assert replacement["reschedule_of"] == ids["booking_id"]
    assert replacement["venue_address"] == "Studio"
    assert replacement["dish_count"] == "4"
    assert replacement["parking_notes"] == "Rear lot"
    assert replacement["style_refs"] == "Warm"
    assert replacement["onsite_contact"] == "Avery"
    assert db.one("SELECT COUNT(*) AS n FROM bookings")["n"] == 2


def test_reschedule_cannot_bypass_notice_policy_with_excluded_booking(policy):
    _, _, _, ids = policy
    db.run("UPDATE event_types SET min_notice_hours=48 WHERE id=?", (ids["event_id"],))
    event = db.one("SELECT * FROM event_types WHERE id=?", (ids["event_id"],))

    with pytest.raises(scheduling.SlotTaken):
        scheduling.book(
            event,
            "2026-07-11 11:00:00",
            "Avery",
            "avery@example.test",
            "",
            "",
            "UTC",
            exclude_id=ids["booking_id"],
        )
    assert db.one("SELECT COUNT(*) AS n FROM bookings")["n"] == 1


def test_reschedule_rechecks_google_busy_before_commit(policy, monkeypatch):
    client, owner_headers, _, ids = policy
    detail = client.get(f"/api/v1/bookings/{ids['booking_id']}", headers=owner_headers)
    monkeypatch.setattr(
        mobile_policy_mutation_api.gcal,
        "free_busy",
        lambda *_args: [
            (
                dt.datetime(2026, 7, 11, 11, tzinfo=dt.UTC),
                dt.datetime(2026, 7, 11, 12, tzinfo=dt.UTC),
            )
        ],
    )

    response = client.post(
        f"/api/v1/bookings/{ids['booking_id']}/reschedule",
        headers=_command(owner_headers, etag=detail.headers["etag"]),
        json={"start_at": "2026-07-11T11:00:00Z", "time_zone": "UTC"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "booking.slot_unavailable"
    assert (
        db.one("SELECT status FROM bookings WHERE id=?", (ids["booking_id"],))["status"]
        == "confirmed"
    )
    assert db.one("SELECT COUNT(*) AS n FROM bookings")["n"] == 1


def test_policy_effect_failure_releases_lease_and_job_retries(policy, monkeypatch):
    client, owner_headers, _, ids = policy
    detail = client.get(f"/api/v1/bookings/{ids['booking_id']}", headers=owner_headers)
    response = client.post(
        f"/api/v1/bookings/{ids['booking_id']}/cancel",
        headers=_command(owner_headers, etag=detail.headers["etag"]),
        json={"reason": "Retry delivery"},
    )
    assert response.status_code == 200
    job = db.one("SELECT id FROM jobs WHERE kind='mobile_policy_effect'")
    attempts = []

    def fail_once(*_args, **_kwargs):
        attempts.append("failed")
        raise RuntimeError("calendar unavailable")

    monkeypatch.setattr(booking_notify, "cancelled", fail_once)
    jobs._execute(int(job["id"]))
    command = db.one(
        """SELECT effects_claimed_at,effects_attempts,effects_last_error
             FROM mobile_commands WHERE operation=?""",
        (f"booking.cancel:{ids['booking_id']}",),
    )
    assert command["effects_claimed_at"] is None
    assert command["effects_attempts"] == 1
    assert "calendar unavailable" in command["effects_last_error"]
    assert db.one("SELECT status FROM jobs WHERE id=?", (job["id"],))["status"] == "queued"

    monkeypatch.setattr(
        booking_notify,
        "cancelled",
        lambda *_args, **_kwargs: attempts.append("delivered"),
    )
    jobs._execute(int(job["id"]))
    command = db.one(
        """SELECT effects_completed_at,effects_attempts,effects_last_error
             FROM mobile_commands WHERE operation=?""",
        (f"booking.cancel:{ids['booking_id']}",),
    )
    assert attempts == ["failed", "delivered"]
    assert command["effects_completed_at"] is not None
    assert command["effects_attempts"] == 2
    assert command["effects_last_error"] is None


def test_dst_fall_back_day_count_uses_both_local_midnights(policy, monkeypatch):
    _, _, _, ids = policy
    monkeypatch.setattr(config, "TIMEZONE", "America/New_York")
    db.run(
        """INSERT INTO bookings
           (token,event_type_id,name,email,start_utc,end_utc,tz)
           VALUES ('dst-late',?,'Late client','late@example.test',
                   '2026-11-02 04:30:00','2026-11-02 05:30:00','America/New_York')""",
        (ids["event_id"],),
    )
    event = db.one("SELECT * FROM event_types WHERE id=?", (ids["event_id"],))

    assert (
        scheduling._day_count(
            db.connect(),
            event,
            dt.date(2026, 11, 1),
        )
        == 1
    )


def test_exact_capability_proposal_accept_is_atomic_and_replay_safe(policy, monkeypatch):
    client, _, proposal_headers, ids = policy
    detail = client.get("/api/v1/client/document", headers=proposal_headers)
    events = []
    fired = []
    monkeypatch.setattr(
        workflows,
        "record_project_event",
        lambda *args, **kwargs: events.append((args, kwargs)),
    )
    monkeypatch.setattr(
        workflows,
        "fire_workflow",
        lambda *args, **kwargs: fired.append((args, kwargs)) or 0,
    )
    key = uuid.uuid4()
    headers = _command(proposal_headers, key=key, etag=detail.headers["etag"])
    accepted = client.post("/api/v1/client/proposal/accept", headers=headers)
    replay = client.post("/api/v1/client/proposal/accept", headers=headers)
    _deliver_next_policy_effect()

    assert accepted.status_code == replay.status_code == 200
    assert accepted.json()["status"] == "accepted"
    assert accepted.json()["can_act"] is False
    assert replay.headers["idempotency-replayed"] == "true"
    assert len(events) == len(fired) == 1
    assert (
        db.one("SELECT status FROM proposals WHERE id=?", (ids["proposal_id"],))["status"]
        == "accepted"
    )
    audit_row = db.one(
        """SELECT actor,diff_json FROM audit_log
            WHERE entity_type='proposal' AND entity_id=? AND action='accept'""",
        (ids["proposal_id"],),
    )
    assert audit_row["actor"] == "mobile_client"

    evidence = json.loads(audit_row["diff_json"])
    assert evidence["session_id"]
    assert evidence["device"]["platform"] == "ios"
    assert evidence["idempotency_key"] == str(key)


def test_proposal_decline_requires_exact_response_scope(policy):
    client, _, proposal_headers, ids = policy
    detail = client.get("/api/v1/client/document", headers=proposal_headers)
    declined = client.post(
        "/api/v1/client/proposal/decline",
        headers=_command(proposal_headers, etag=detail.headers["etag"]),
    )
    assert declined.status_code == 200
    assert declined.json()["status"] == "declined"

    session = db.one(
        """SELECT id FROM api_sessions
            WHERE principal_kind='document_guest' AND resource_id=?""",
        (ids["proposal_id"],),
    )
    db.run(
        "UPDATE api_sessions SET scopes_json=? WHERE id=?",
        (
            f'["document:proposal:{ids["proposal_id"]}:read"]',
            session["id"],
        ),
    )
    denied = client.post(
        "/api/v1/client/proposal/decline",
        headers={
            **proposal_headers,
            "Idempotency-Key": str(uuid.uuid4()),
            "If-Match": declined.headers["etag"],
        },
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "auth.insufficient_scope"


def test_policy_audit_failure_rolls_back_booking_and_command(policy, monkeypatch):
    client, owner_headers, _, ids = policy
    detail = client.get(f"/api/v1/bookings/{ids['booking_id']}", headers=owner_headers)

    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(audit, "log", fail_audit)
    response = client.post(
        f"/api/v1/bookings/{ids['booking_id']}/cancel",
        headers=_command(owner_headers, etag=detail.headers["etag"]),
        json={"reason": "Should roll back"},
    )
    assert response.status_code == 500
    assert (
        db.one("SELECT status FROM bookings WHERE id=?", (ids["booking_id"],))["status"]
        == "confirmed"
    )
    assert db.one("SELECT COUNT(*) AS n FROM mobile_commands")["n"] == 0
