"""Native owner booking reschedule command (queue S6c).

The command reserves a session-bound Idempotency-Key, revalidates the destination
under an immediate SQLite writer lock, and commits the replacement, source
cancellation, audit evidence, and immutable replay response together.
"""

import datetime as dt
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app import (
    audit,
    booking_notify,
    config,
    db,
    mobile_auth,
    mobile_idempotency,
    ratelimit,
    scheduling,
)
from app.main import app

pytestmark = pytest.mark.unit

_NOW = dt.datetime(2026, 7, 13, 12, 0, tzinfo=dt.UTC)
_SOURCE_START = "2026-07-15 10:00:00"
_TARGET_START = "2026-07-16 11:00:00"
_TARGET_RFC3339 = "2026-07-16T11:00:00Z"


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "SECRET_KEY", "booking-reschedule-secret")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "owner-password")
    monkeypatch.setattr(config, "BASE_URL", "https://studio.test")
    monkeypatch.setattr(config, "TIMEZONE", "UTC")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "mise.db")
    monkeypatch.setattr(scheduling, "now_utc", lambda: _NOW)
    ratelimit._hits.clear()
    db.migrate()

    client = TestClient(app, base_url="https://studio.test")
    login = _login(client)
    payload = login.json()
    yield {
        "client": client,
        "headers": {"Authorization": f"Bearer {payload['access_token']}"},
        "login": payload,
    }
    client.close()
    ratelimit._hits.clear()


@pytest.fixture
def confirmation_spy(monkeypatch):
    calls: list[int] = []

    def confirm(booking_id: int) -> None:
        # Effects must run only after all database evidence is visible to a new
        # connection, never while the command transaction is half-written.
        assert db.one("SELECT status FROM bookings WHERE id=?", (booking_id,))["status"] == (
            "confirmed"
        )
        assert db.one("SELECT COUNT(*) AS n FROM api_idempotency_replays")["n"] == 1
        assert db.one("SELECT COUNT(*) AS n FROM audit_log WHERE entity_type='booking'")["n"] == 2
        calls.append(booking_id)

    monkeypatch.setattr(booking_notify, "confirm", confirm)
    return calls


def _login(client: TestClient) -> object:
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
    return response


def _seed_event(*, max_per_day: int = 0, start_min: int = 0, end_min: int = 1440) -> int:
    event_id = db.run(
        """INSERT INTO event_types
           (slug,name,description,duration_min,location,color,buffer_before_min,
            buffer_after_min,min_notice_hours,max_per_day,booking_window_days,
            slot_step_min,active,position)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,1)""",
        (
            f"session-{uuid4().hex}",
            "Studio session",
            "",
            60,
            "Studio",
            "#123ABC",
            0,
            0,
            0,
            max_per_day,
            30,
            60,
        ),
    )
    for weekday in range(7):
        db.run(
            """INSERT INTO availability_rules
               (event_type_id,weekday,start_min,end_min) VALUES (?,?,?,?)""",
            (event_id, weekday, start_min, end_min),
        )
    return event_id


def _plus_hour(value: str) -> str:
    parsed = dt.datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    return (parsed + dt.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")


def _seed_booking(
    event_id: int,
    *,
    start: str = _SOURCE_START,
    status: str = "confirmed",
) -> tuple[int, int, int]:
    client_id = db.run(
        "INSERT INTO clients (name,email,phone) VALUES (?,?,?)",
        ("Rossi Trattoria", f"ops-{uuid4().hex}@rossi.test", "555-0142"),
    )
    project_id = db.run(
        "INSERT INTO projects (client_id,title,shoot_date) VALUES (?,?,?)",
        (client_id, "Rossi menu", start[:10]),
    )
    booking_id = db.run(
        """INSERT INTO bookings
           (token,event_type_id,name,email,phone,notes,start_utc,end_utc,tz,status,
            client_id,project_id,venue_address,dish_count,parking_notes,
            style_refs,onsite_contact)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            f"bk-{uuid4().hex}",
            event_id,
            "Rossi Trattoria",
            "ops@rossi.test",
            "555-0142",
            "Spring menu launch.",
            start,
            _plus_hour(start),
            "America/New_York",
            status,
            client_id,
            project_id,
            "12 Vine St",
            "40",
            "Loading dock",
            "Bright and airy",
            "Lou 555-0199",
        ),
    )
    return booking_id, client_id, project_id


def _reschedule(
    owner,
    booking_id: int,
    *,
    key: UUID | str | None = None,
    start_at: str = _TARGET_RFC3339,
    time_zone: str = "America/New_York",
):
    headers = dict(owner["headers"])
    if key is not None:
        headers["Idempotency-Key"] = str(key)
    return owner["client"].post(
        f"/api/v1/bookings/{booking_id}/reschedule",
        headers=headers,
        json={"start_at": start_at, "time_zone": time_zone},
    )


def _booking_audits() -> list:
    return db.all_(
        """SELECT entity_id, action, actor, diff_json
             FROM audit_log WHERE entity_type='booking' ORDER BY id"""
    )


def test_reschedule_commits_transition_replay_and_carryover(
    owner,
    confirmation_spy,
):
    event_id = _seed_event()
    booking_id, client_id, project_id = _seed_booking(event_id)
    key = uuid4()

    response = _reschedule(owner, booking_id, key=key)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["vary"] == "Authorization"
    body = response.json()
    assert body == {
        "status": "rescheduled",
        "original_booking_id": booking_id,
        "replacement_booking_id": body["replacement_booking_id"],
        "start_at": _TARGET_RFC3339,
        "end_at": "2026-07-16T12:00:00Z",
    }
    replacement_id = body["replacement_booking_id"]

    source = db.one(
        "SELECT status,cancel_reason,cancelled_at FROM bookings WHERE id=?",
        (booking_id,),
    )
    assert source["status"] == "cancelled"
    assert source["cancel_reason"] == "Rescheduled from the studio app"
    assert source["cancelled_at"] is not None

    replacement = db.one("SELECT * FROM bookings WHERE id=?", (replacement_id,))
    assert replacement["status"] == "confirmed"
    assert replacement["reschedule_of"] == booking_id
    assert replacement["start_utc"] == _TARGET_START
    assert replacement["end_utc"] == "2026-07-16 12:00:00"
    assert replacement["tz"] == "America/New_York"
    assert replacement["client_id"] == client_id
    assert replacement["project_id"] == project_id
    assert replacement["venue_address"] == "12 Vine St"
    assert replacement["dish_count"] == "40"
    assert replacement["parking_notes"] == "Loading dock"
    assert replacement["style_refs"] == "Bright and airy"
    assert replacement["onsite_contact"] == "Lou 555-0199"
    assert replacement["reminded_48h"] == 0
    assert replacement["reminded_24h"] == 0
    assert replacement["armed_postshoot"] == 0

    audits = _booking_audits()
    assert [(row["entity_id"], row["action"], row["actor"]) for row in audits] == [
        (booking_id, "reschedule", "owner"),
        (replacement_id, "reschedule_create", "owner"),
    ]
    source_diff = json.loads(audits[0]["diff_json"])
    replacement_diff = json.loads(audits[1]["diff_json"])
    assert source_diff["replacement_booking_id"] == replacement_id
    assert source_diff["start_utc"] == [_SOURCE_START, _TARGET_START]
    assert source_diff["status"] == ["confirmed", "cancelled"]
    assert replacement_diff["source_booking_id"] == booking_id
    assert replacement_diff["status"] == [None, "confirmed"]
    assert replacement_diff["session_id"] == source_diff["session_id"]

    columns = {row["name"] for row in db.all_("PRAGMA table_info(api_idempotency_replays)")}
    assert "key_hash" in columns
    assert "idempotency_key" not in columns
    receipt = db.one("SELECT * FROM api_idempotency_replays")
    assert receipt["key_hash"] != str(key)
    assert len(receipt["key_hash"]) == 64
    assert json.loads(receipt["response_json"]) == body
    assert str(key) not in receipt["response_json"]
    assert receipt["expires_at"] > receipt["created_at"]
    assert receipt["session_id"] == source_diff["session_id"]
    stored_evidence = "".join(row["diff_json"] for row in audits) + receipt["response_json"]
    for sensitive in (
        str(key),
        replacement["token"],
        "ops@rossi.test",
        "555-0142",
        "Spring menu launch.",
    ):
        assert sensitive not in stored_evidence
    assert confirmation_spy == [replacement_id]


def test_expired_receipts_are_physically_pruned(owner, confirmation_spy):
    event_id = _seed_event()
    booking_id, _, _ = _seed_booking(event_id)
    assert _reschedule(owner, booking_id, key=uuid4()).status_code == 200
    receipt = db.one("SELECT created_at, expires_at FROM api_idempotency_replays")

    assert mobile_idempotency.prune_expired(cutoff=receipt["expires_at"] - 1) == 0
    assert mobile_idempotency.prune_expired(cutoff=receipt["expires_at"]) == 1
    assert db.one("SELECT COUNT(*) AS n FROM api_idempotency_replays")["n"] == 0


def test_equivalent_timestamp_replays_exact_response_across_access_refresh(
    owner,
    confirmation_spy,
):
    event_id = _seed_event()
    booking_id, _, _ = _seed_booking(event_id)
    key = uuid4()

    first = _reschedule(
        owner,
        booking_id,
        key=key,
        start_at="2026-07-16T11:00:00.000Z",
    )
    refreshed = owner["client"].post(
        "/api/v1/auth/refresh",
        json={"refresh_token": owner["login"]["refresh_token"]},
    )
    assert refreshed.status_code == 200
    owner["headers"] = {"Authorization": f"Bearer {refreshed.json()['access_token']}"}
    replay = _reschedule(
        owner,
        booking_id,
        key=key,
        start_at="2026-07-16T07:00:00-04:00",
    )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.content == first.content
    assert replay.headers["cache-control"] == first.headers["cache-control"] == "no-store"
    assert replay.headers["vary"] == first.headers["vary"] == "Authorization"
    assert (
        db.one("SELECT COUNT(*) AS n FROM bookings WHERE reschedule_of=?", (booking_id,))["n"] == 1
    )
    assert len(_booking_audits()) == 2
    assert len(confirmation_spy) == 1


def test_same_key_with_different_request_conflicts_before_source_state(
    owner,
    confirmation_spy,
):
    event_id = _seed_event()
    booking_id, _, _ = _seed_booking(event_id)
    key = uuid4()
    assert _reschedule(owner, booking_id, key=key).status_code == 200

    conflict = _reschedule(
        owner,
        booking_id,
        key=key,
        start_at="2026-07-16T12:00:00Z",
    )

    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency.key_conflict"
    assert len(confirmation_spy) == 1
    assert db.one("SELECT COUNT(*) AS n FROM api_idempotency_replays")["n"] == 1


def test_new_key_cannot_reschedule_a_stale_source(owner, confirmation_spy):
    event_id = _seed_event()
    booking_id, _, _ = _seed_booking(event_id)
    assert _reschedule(owner, booking_id, key=uuid4()).status_code == 200

    stale = _reschedule(
        owner,
        booking_id,
        key=uuid4(),
        start_at="2026-07-16T12:00:00Z",
    )

    assert stale.status_code == 409
    assert stale.json()["code"] == "booking.not_reschedulable"
    assert len(confirmation_spy) == 1


@pytest.mark.parametrize("key", [None, "not-a-uuid", "00000000-0000-0000-0000"])
def test_idempotency_key_is_required_uuid(owner, monkeypatch, key):
    monkeypatch.setattr(booking_notify, "confirm", lambda *_: pytest.fail("must not notify"))
    event_id = _seed_event()
    booking_id, _, _ = _seed_booking(event_id)

    response = _reschedule(owner, booking_id, key=key)

    assert response.status_code == 422
    if key is not None:
        assert str(key) not in response.text
    assert db.one("SELECT status FROM bookings WHERE id=?", (booking_id,))["status"] == (
        "confirmed"
    )
    assert db.one("SELECT COUNT(*) AS n FROM api_idempotency_replays")["n"] == 0


@pytest.mark.parametrize(
    ("start_at", "time_zone"),
    [
        ("2026-07-16T11:00:00", "UTC"),
        ("2026-07-16T11:00:00.250Z", "UTC"),
        ("2026-07-16T11:00Z", "UTC"),
        ("2026-07-16T11:00:00.0000001Z", "UTC"),
        ("20260716T110000Z", "UTC"),
        ("2026-W29-4T11:00:00Z", "UTC"),
        (_TARGET_RFC3339, "Mars/Olympus"),
    ],
)
def test_request_requires_aware_whole_second_and_iana_zone(
    owner,
    monkeypatch,
    start_at,
    time_zone,
):
    monkeypatch.setattr(booking_notify, "confirm", lambda *_: pytest.fail("must not notify"))
    event_id = _seed_event()
    booking_id, _, _ = _seed_booking(event_id)

    response = _reschedule(
        owner,
        booking_id,
        key=uuid4(),
        start_at=start_at,
        time_zone=time_zone,
    )

    assert response.status_code == 422
    assert db.one("SELECT status FROM bookings WHERE id=?", (booking_id,))["status"] == (
        "confirmed"
    )
    assert (
        db.one("SELECT COUNT(*) AS n FROM bookings WHERE reschedule_of=?", (booking_id,))["n"] == 0
    )


def test_unknown_same_time_cancelled_and_inactive_sources_fail_closed(owner, monkeypatch):
    monkeypatch.setattr(booking_notify, "confirm", lambda *_: pytest.fail("must not notify"))
    event_id = _seed_event()
    same_time_id, _, _ = _seed_booking(event_id)
    cancelled_id, _, _ = _seed_booking(
        event_id,
        start="2026-07-15 13:00:00",
        status="cancelled",
    )
    inactive_id, _, _ = _seed_booking(event_id, start="2026-07-15 16:00:00")

    unknown = _reschedule(owner, 999999, key=uuid4())
    same = _reschedule(
        owner,
        same_time_id,
        key=uuid4(),
        start_at="2026-07-15T10:00:00Z",
    )
    cancelled = _reschedule(owner, cancelled_id, key=uuid4())
    db.run("UPDATE event_types SET active=0 WHERE id=?", (event_id,))
    inactive = _reschedule(owner, inactive_id, key=uuid4())

    assert unknown.status_code == 404
    assert same.status_code == 409
    assert same.json()["code"] == "booking.unchanged"
    assert cancelled.status_code == 409
    assert cancelled.json()["code"] == "booking.not_reschedulable"
    assert inactive.status_code == 409
    assert inactive.json()["code"] == "booking.event_unavailable"
    assert db.one("SELECT COUNT(*) AS n FROM api_idempotency_replays")["n"] == 0


def test_reschedule_cannot_bypass_availability_policy(owner, monkeypatch):
    monkeypatch.setattr(booking_notify, "confirm", lambda *_: pytest.fail("must not notify"))
    event_id = _seed_event(start_min=9 * 60, end_min=17 * 60)
    booking_id, _, _ = _seed_booking(event_id)

    forged = _reschedule(
        owner,
        booking_id,
        key=uuid4(),
        start_at="2026-07-16T03:00:00Z",
        time_zone="UTC",
    )

    assert forged.status_code == 409
    assert forged.json()["code"] == "booking.slot_unavailable"
    assert db.one("SELECT status FROM bookings WHERE id=?", (booking_id,))["status"] == (
        "confirmed"
    )
    assert (
        db.one("SELECT COUNT(*) AS n FROM bookings WHERE reschedule_of=?", (booking_id,))["n"] == 0
    )
    assert _booking_audits() == []


def test_same_day_reschedule_excludes_source_from_daily_cap(
    owner,
    confirmation_spy,
):
    event_id = _seed_event(max_per_day=1, start_min=9 * 60, end_min=17 * 60)
    booking_id, _, _ = _seed_booking(event_id)

    response = _reschedule(
        owner,
        booking_id,
        key=uuid4(),
        start_at="2026-07-15T11:00:00Z",
        time_zone="UTC",
    )

    assert response.status_code == 200
    assert response.json()["start_at"] == "2026-07-15T11:00:00Z"
    assert len(confirmation_spy) == 1


def test_dst_slots_skip_spring_gap_and_preserve_fall_fold(owner, monkeypatch):
    monkeypatch.setattr(config, "TIMEZONE", "America/New_York")
    event_id = _seed_event(start_min=60, end_min=4 * 60)
    event_type = db.one("SELECT * FROM event_types WHERE id=?", (event_id,))
    con = db.connect()
    try:
        spring = scheduling._slots_utc(
            con,
            event_type,
            dt.date(2026, 3, 8),
            dt.datetime(2026, 3, 1, tzinfo=dt.UTC),
        )
        fall = scheduling._slots_utc(
            con,
            event_type,
            dt.date(2026, 11, 1),
            dt.datetime(2026, 10, 25, tzinfo=dt.UTC),
        )
    finally:
        con.close()

    eastern = ZoneInfo("America/New_York")
    spring_local = [value.astimezone(eastern) for value in spring]
    fall_local = [value.astimezone(eastern) for value in fall]
    assert [(value.hour, value.fold) for value in spring_local] == [
        (1, 0),
        (3, 0),
    ]
    assert [(value.hour, value.fold) for value in fall_local] == [
        (1, 0),
        (1, 1),
        (2, 0),
        (3, 0),
    ]


def test_dst_day_cap_uses_consecutive_local_midnights(owner, monkeypatch):
    monkeypatch.setattr(config, "TIMEZONE", "America/New_York")
    event_id = _seed_event(max_per_day=1)
    late_fall_id, _, _ = _seed_booking(event_id, start="2026-11-02 04:30:00")
    _seed_booking(event_id, start="2026-03-09 04:30:00")
    event_type = db.one("SELECT * FROM event_types WHERE id=?", (event_id,))

    con = db.connect()
    try:
        assert scheduling._day_count(con, event_type, dt.date(2026, 11, 1)) == 1
        assert (
            scheduling._day_count(
                con,
                event_type,
                dt.date(2026, 11, 1),
                exclude_id=late_fall_id,
            )
            == 0
        )
        assert scheduling._day_count(con, event_type, dt.date(2026, 3, 8)) == 0
    finally:
        con.close()


def test_dst_fold_slots_have_distinct_rendered_labels(owner, monkeypatch):
    monkeypatch.setattr(config, "TIMEZONE", "America/New_York")
    monkeypatch.setattr(
        scheduling,
        "now_utc",
        lambda: dt.datetime(2026, 10, 25, 12, 0, tzinfo=dt.UTC),
    )
    monkeypatch.setattr(scheduling.gcal, "free_busy", lambda *_: None)
    event_id = _seed_event(start_min=60, end_min=4 * 60)
    event_type = db.one("SELECT * FROM event_types WHERE id=?", (event_id,))

    slots = scheduling.slots_for_day(event_type, dt.date(2026, 11, 1))

    assert [slot["label"] for slot in slots] == [
        "1:00 AM (UTC-04:00)",
        "1:00 AM (UTC-05:00)",
        "2:00 AM",
        "3:00 AM",
    ]


def test_competing_booking_keeps_source_confirmed(owner, monkeypatch):
    monkeypatch.setattr(booking_notify, "confirm", lambda *_: pytest.fail("must not notify"))
    event_id = _seed_event()
    source_id, _, _ = _seed_booking(event_id)
    _seed_booking(event_id, start=_TARGET_START)

    response = _reschedule(owner, source_id, key=uuid4())

    assert response.status_code == 409
    assert response.json()["code"] == "booking.slot_unavailable"
    assert db.one("SELECT status FROM bookings WHERE id=?", (source_id,))["status"] == ("confirmed")
    assert (
        db.one("SELECT COUNT(*) AS n FROM bookings WHERE reschedule_of=?", (source_id,))["n"] == 0
    )


def test_failure_after_first_audit_rolls_back_every_command_write(
    owner,
    monkeypatch,
):
    event_id = _seed_event()
    booking_id, _, _ = _seed_booking(event_id)
    real_log = audit.log
    calls = 0

    def fail_second_audit(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("audit unavailable")
        return real_log(*args, **kwargs)

    monkeypatch.setattr(audit, "log", fail_second_audit)
    monkeypatch.setattr(booking_notify, "confirm", lambda *_: pytest.fail("must not notify"))

    response = _reschedule(owner, booking_id, key=uuid4())

    assert response.status_code == 500
    assert db.one("SELECT status FROM bookings WHERE id=?", (booking_id,))["status"] == (
        "confirmed"
    )
    assert (
        db.one("SELECT COUNT(*) AS n FROM bookings WHERE reschedule_of=?", (booking_id,))["n"] == 0
    )
    assert _booking_audits() == []
    assert db.one("SELECT COUNT(*) AS n FROM api_idempotency_replays")["n"] == 0


def test_post_commit_effect_failure_does_not_corrupt_replay(owner, monkeypatch):
    event_id = _seed_event()
    booking_id, _, _ = _seed_booking(event_id)
    key = uuid4()
    calls = 0

    def fail_effect(_booking_id: int) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(booking_notify, "confirm", fail_effect)
    first = _reschedule(owner, booking_id, key=key)
    replay = _reschedule(owner, booking_id, key=key)

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.content == first.content
    assert calls == 1
    assert db.one("SELECT COUNT(*) AS n FROM api_idempotency_replays")["n"] == 1


def test_missing_bearer_guest_and_read_only_owner_cannot_reschedule(owner, monkeypatch):
    monkeypatch.setattr(booking_notify, "confirm", lambda *_: pytest.fail("must not notify"))
    event_id = _seed_event()
    booking_id, _, _ = _seed_booking(event_id)
    key = uuid4()

    missing = owner["client"].post(
        f"/api/v1/bookings/{booking_id}/reschedule",
        headers={"Idempotency-Key": str(key)},
        json={"start_at": _TARGET_RFC3339, "time_zone": "UTC"},
    )
    assert missing.status_code == 401

    session = db.one("SELECT * FROM api_sessions ORDER BY created_at DESC LIMIT 1")
    guest = mobile_auth.Principal(
        session_id="guest-session",
        tenant_key=session["tenant_key"],
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
    guest_refused = _reschedule(owner, booking_id, key=key)
    assert guest_refused.status_code == 403
    assert guest_refused.json()["code"] == "auth.insufficient_scope"

    read_only = mobile_auth.Principal(
        session_id=session["id"],
        tenant_key=session["tenant_key"],
        kind=mobile_auth.STUDIO_OWNER,
        resource_id=None,
        resource_variant=None,
        gallery_visitor_id=None,
        scopes=frozenset({"studio:read"}),
        device_name=None,
        device_platform=None,
        device_app_version=None,
        created_at=dt.datetime.fromtimestamp(session["created_at"], tz=dt.UTC),
        absolute_expires_at=dt.datetime.fromtimestamp(
            session["absolute_expires_at"],
            tz=dt.UTC,
        ),
    )
    monkeypatch.setattr(mobile_auth, "authenticate_request", lambda *a, **k: read_only)
    refused = _reschedule(owner, booking_id, key=key)

    assert refused.status_code == 403
    assert refused.json()["code"] == "auth.insufficient_scope"
    assert db.one("SELECT status FROM bookings WHERE id=?", (booking_id,))["status"] == (
        "confirmed"
    )
    assert db.one("SELECT COUNT(*) AS n FROM api_idempotency_replays")["n"] == 0
    assert _booking_audits() == []
    assert (
        db.one("SELECT COUNT(*) AS n FROM bookings WHERE reschedule_of=?", (booking_id,))["n"] == 0
    )


def test_session_expiry_is_rechecked_inside_command_transaction(owner, monkeypatch):
    monkeypatch.setattr(booking_notify, "confirm", lambda *_: pytest.fail("must not notify"))
    event_id = _seed_event()
    booking_id, _, _ = _seed_booking(event_id)
    session = db.one("SELECT * FROM api_sessions ORDER BY created_at DESC LIMIT 1")
    monkeypatch.setattr(
        mobile_idempotency,
        "now_ts",
        lambda: int(session["absolute_expires_at"]) + 1,
    )
    stale_principal = mobile_auth.Principal(
        session_id=session["id"],
        tenant_key=session["tenant_key"],
        kind=mobile_auth.STUDIO_OWNER,
        resource_id=None,
        resource_variant=None,
        gallery_visitor_id=None,
        scopes=frozenset({"studio:read", "studio:write"}),
        device_name=None,
        device_platform=None,
        device_app_version=None,
        created_at=dt.datetime.fromtimestamp(session["created_at"], tz=dt.UTC),
        absolute_expires_at=dt.datetime.fromtimestamp(session["absolute_expires_at"], tz=dt.UTC),
    )
    monkeypatch.setattr(mobile_auth, "authenticate_request", lambda *a, **k: stale_principal)

    response = _reschedule(owner, booking_id, key=uuid4())

    assert response.status_code == 401
    assert response.json()["code"] == "auth.invalid_token"
    assert db.one("SELECT status FROM bookings WHERE id=?", (booking_id,))["status"] == "confirmed"
    assert db.one("SELECT COUNT(*) AS n FROM api_idempotency_replays")["n"] == 0
    assert _booking_audits() == []


def test_near_expiry_receipt_uses_post_lock_authorization_time(
    owner,
    confirmation_spy,
    monkeypatch,
):
    event_id = _seed_event()
    booking_id, _, _ = _seed_booking(event_id)
    session = db.one("SELECT * FROM api_sessions ORDER BY created_at DESC LIMIT 1")
    authorized_at = int(session["absolute_expires_at"]) - 1
    monkeypatch.setattr(mobile_idempotency, "now_ts", lambda: authorized_at)

    response = _reschedule(owner, booking_id, key=uuid4())

    assert response.status_code == 200
    receipt = db.one("SELECT created_at, expires_at FROM api_idempotency_replays")
    assert receipt["created_at"] == authorized_at
    assert receipt["expires_at"] == authorized_at + 1
    assert len(confirmation_spy) == 1


def test_concurrent_identical_retries_create_one_replacement(
    owner,
    confirmation_spy,
):
    event_id = _seed_event()
    booking_id, _, _ = _seed_booking(event_id)
    key = uuid4()
    barrier = threading.Barrier(2)

    def submit() -> tuple[int, bytes]:
        barrier.wait()
        response = _reschedule(owner, booking_id, key=key)
        return response.status_code, response.content

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: submit(), range(2)))

    assert [status for status, _ in responses] == [200, 200]
    assert responses[0][1] == responses[1][1]
    assert (
        db.one("SELECT COUNT(*) AS n FROM bookings WHERE reschedule_of=?", (booking_id,))["n"] == 1
    )
    assert len(_booking_audits()) == 2
    assert len(confirmation_spy) == 1


def test_concurrent_distinct_keys_cannot_reschedule_source_twice(
    owner,
    confirmation_spy,
):
    event_id = _seed_event()
    booking_id, _, _ = _seed_booking(event_id)
    keys = (uuid4(), uuid4())
    barrier = threading.Barrier(2)

    def submit(key: UUID) -> tuple[int, str | None]:
        barrier.wait()
        response = _reschedule(owner, booking_id, key=key)
        return response.status_code, response.json().get("code")

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, keys))

    assert sorted(status for status, _ in responses) == [200, 409]
    assert {code for _, code in responses} == {None, "booking.not_reschedulable"}
    assert (
        db.one("SELECT COUNT(*) AS n FROM bookings WHERE reschedule_of=?", (booking_id,))["n"] == 1
    )
    assert db.one("SELECT COUNT(*) AS n FROM api_idempotency_replays")["n"] == 1
    assert len(_booking_audits()) == 2
    assert len(confirmation_spy) == 1


def test_concurrent_sources_cannot_claim_one_destination(owner, confirmation_spy):
    event_id = _seed_event()
    source_ids = (
        _seed_booking(event_id)[0],
        _seed_booking(event_id, start="2026-07-15 13:00:00")[0],
    )
    keys = (uuid4(), uuid4())
    barrier = threading.Barrier(2)

    def submit(item: tuple[int, UUID]) -> tuple[int, dict]:
        barrier.wait()
        response = _reschedule(owner, item[0], key=item[1])
        return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, zip(source_ids, keys, strict=True)))

    assert sorted(status for status, _ in responses) == [200, 409]
    assert [body["code"] for status, body in responses if status == 409] == [
        "booking.slot_unavailable"
    ]
    winner = next(body["original_booking_id"] for status, body in responses if status == 200)
    loser = next(source_id for source_id in source_ids if source_id != winner)
    assert db.one("SELECT status FROM bookings WHERE id=?", (winner,))["status"] == "cancelled"
    assert db.one("SELECT status FROM bookings WHERE id=?", (loser,))["status"] == "confirmed"
    assert db.one("SELECT COUNT(*) AS n FROM api_idempotency_replays")["n"] == 1
    assert len(_booking_audits()) == 2
    assert len(confirmation_spy) == 1
