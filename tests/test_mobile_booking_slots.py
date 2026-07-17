"""Source-aware native booking availability reads (S6f)."""

import datetime as dt
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import config, db, gcal, mobile_auth, ratelimit, scheduling
from app.main import app

pytestmark = pytest.mark.unit

_NOW = dt.datetime(2026, 7, 13, 12, 0, tzinfo=dt.UTC)


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "SECRET_KEY", "booking-slots-secret")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "owner-password")
    monkeypatch.setattr(config, "BASE_URL", "https://studio.test")
    monkeypatch.setattr(config, "TIMEZONE", "UTC")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "mise.db")
    monkeypatch.setattr(scheduling, "now_utc", lambda: _NOW)
    monkeypatch.setattr(scheduling.gcal, "free_busy", lambda *_: None)
    ratelimit._hits.clear()
    db.migrate()

    client = TestClient(app, base_url="https://studio.test")
    login = client.post(
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
    assert login.status_code == 200
    yield {
        "client": client,
        "headers": {"Authorization": f"Bearer {login.json()['access_token']}"},
    }
    client.close()
    ratelimit._hits.clear()


def _seed_event(
    *,
    max_per_day: int = 0,
    start_min: int = 9 * 60,
    end_min: int = 12 * 60,
    duration_min: int = 60,
    slot_step_min: int = 60,
    active: bool = True,
) -> int:
    event_id = db.run(
        """INSERT INTO event_types
           (slug,name,description,duration_min,location,color,buffer_before_min,
            buffer_after_min,min_notice_hours,max_per_day,booking_window_days,
            slot_step_min,active,position)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
        (
            f"session-{uuid4().hex}",
            "Studio session",
            "",
            duration_min,
            "Studio",
            "#123ABC",
            0,
            0,
            0,
            max_per_day,
            30,
            slot_step_min,
            int(active),
        ),
    )
    for weekday in range(7):
        db.run(
            """INSERT INTO availability_rules
               (event_type_id,weekday,start_min,end_min) VALUES (?,?,?,?)""",
            (event_id, weekday, start_min, end_min),
        )
    return event_id


def _seed_booking(
    event_id: int,
    *,
    start: str = "2026-07-15 10:00:00",
    status: str = "confirmed",
    google_event_id: str | None = None,
) -> int:
    parsed = dt.datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
    end = (parsed + dt.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    return db.run(
        """INSERT INTO bookings
           (token,event_type_id,name,email,start_utc,end_utc,tz,status,google_event_id)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            f"bk-{uuid4().hex}",
            event_id,
            "Rossi Trattoria",
            f"ops-{uuid4().hex}@rossi.test",
            start,
            end,
            "America/New_York",
            status,
            google_event_id,
        ),
    )


def _slots(owner, event_id: int, day: str, source_id: int | None = None):
    params: dict[str, str | int] = {"day": day}
    if source_id is not None:
        params["reschedule_booking_id"] = source_id
    return owner["client"].get(
        f"/api/v1/event-types/{event_id}/slots",
        headers=owner["headers"],
        params=params,
    )


def test_generic_slots_are_sorted_utc_private_and_contact_free(owner):
    event_id = _seed_event()

    response = _slots(owner, event_id, "2026-07-15")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["vary"] == "Authorization"
    assert response.json() == {
        "event_type_id": event_id,
        "day": "2026-07-15",
        "time_zone": "UTC",
        "reschedule_booking_id": None,
        "slots": [
            {
                "start_at": "2026-07-15T09:00:00Z",
                "end_at": "2026-07-15T10:00:00Z",
            },
            {
                "start_at": "2026-07-15T10:00:00Z",
                "end_at": "2026-07-15T11:00:00Z",
            },
            {
                "start_at": "2026-07-15T11:00:00Z",
                "end_at": "2026-07-15T12:00:00Z",
            },
        ],
    }
    serialized = response.text
    for forbidden in ("Rossi", "@rossi", "token", "phone", "notes"):
        assert forbidden not in serialized


def test_source_exclusion_releases_same_day_cap_but_omits_unchanged(owner):
    event_id = _seed_event(max_per_day=1)
    source_id = _seed_booking(event_id)

    capped = _slots(owner, event_id, "2026-07-15")
    preview = _slots(owner, event_id, "2026-07-15", source_id)

    assert capped.status_code == 200
    assert capped.json()["slots"] == []
    assert preview.status_code == 200
    assert preview.json()["reschedule_booking_id"] == source_id
    assert [slot["start_at"] for slot in preview.json()["slots"]] == [
        "2026-07-15T09:00:00Z",
        "2026-07-15T11:00:00Z",
    ]


def test_another_booking_still_consumes_destination_day_cap(owner):
    event_id = _seed_event(max_per_day=1)
    source_id = _seed_booking(event_id)
    _seed_booking(event_id, start="2026-07-16 09:00:00")

    response = _slots(owner, event_id, "2026-07-16", source_id)

    assert response.status_code == 200
    assert response.json()["slots"] == []


def test_google_busy_interval_filters_the_advisory_feed(owner, monkeypatch):
    event_id = _seed_event()
    busy_start = dt.datetime(2026, 7, 15, 10, 0, tzinfo=dt.UTC)
    busy_end = dt.datetime(2026, 7, 15, 11, 0, tzinfo=dt.UTC)
    monkeypatch.setattr(
        scheduling.gcal,
        "free_busy",
        lambda *_: [(busy_start, busy_end)],
    )

    response = _slots(owner, event_id, "2026-07-15")

    assert response.status_code == 200
    assert [slot["start_at"] for slot in response.json()["slots"]] == [
        "2026-07-15T09:00:00Z",
        "2026-07-15T11:00:00Z",
    ]


def test_google_source_event_is_released_without_releasing_external_busy(owner, monkeypatch):
    event_id = _seed_event(slot_step_min=30)
    source_id = _seed_booking(event_id, google_event_id="google-source")
    calls: list[tuple[dt.datetime, dt.datetime, str]] = []

    def excluding(start: dt.datetime, end: dt.datetime, event_id: str):
        calls.append((start, end, event_id))
        return [
            (
                dt.datetime(2026, 7, 15, 10, 30, tzinfo=dt.UTC),
                dt.datetime(2026, 7, 15, 11, 30, tzinfo=dt.UTC),
            )
        ]

    monkeypatch.setattr(gcal, "free_busy_excluding_event", excluding)

    response = _slots(owner, event_id, "2026-07-15", source_id)

    assert response.status_code == 200
    assert [slot["start_at"] for slot in response.json()["slots"]] == [
        "2026-07-15T09:00:00Z",
        "2026-07-15T09:30:00Z",
    ]
    assert calls == [
        (
            dt.datetime(2026, 7, 15, 0, 0, tzinfo=dt.UTC),
            dt.datetime(2026, 7, 16, 0, 0, tzinfo=dt.UTC),
            "google-source",
        )
    ]


def test_google_event_exclusion_requires_complete_freebusy_reconciliation(monkeypatch):
    start = dt.datetime(2026, 7, 15, tzinfo=dt.UTC)
    end = start + dt.timedelta(days=1)
    source = (start + dt.timedelta(hours=10), start + dt.timedelta(hours=11))
    external = (
        start + dt.timedelta(hours=10, minutes=30),
        start + dt.timedelta(hours=11, minutes=30),
    )
    canonical = [(source[0], external[1])]
    monkeypatch.setattr(gcal, "free_busy", lambda *_: canonical)
    monkeypatch.setattr(
        gcal,
        "_busy_events",
        lambda *_: [
            ("google-source", *source),
            ("external", *external),
        ],
    )

    assert gcal.free_busy_excluding_event(start, end, "google-source") == [external]

    monkeypatch.setattr(gcal, "_busy_events", lambda *_: [("google-source", *source)])
    assert gcal.free_busy_excluding_event(start, end, "google-source") == canonical


def test_google_busy_event_listing_pages_and_normalizes_event_shapes(monkeypatch):
    monkeypatch.setattr(config, "TIMEZONE", "America/New_York")
    start = dt.datetime(2026, 7, 15, tzinfo=dt.UTC)
    end = start + dt.timedelta(days=1)
    paths: list[str] = []

    def api(method: str, path: str, *, timeout: float):
        assert method == "GET"
        assert timeout == 5
        paths.append(path)
        if "pageToken=" not in path:
            return {
                "timeZone": "UTC",
                "items": [
                    {
                        "id": "timed",
                        "start": {"dateTime": "2026-07-15T10:00:00Z"},
                        "end": {"dateTime": "2026-07-15T11:00:00Z"},
                    },
                    {
                        "id": "available",
                        "transparency": "transparent",
                        "start": {"dateTime": "2026-07-15T12:00:00Z"},
                        "end": {"dateTime": "2026-07-15T13:00:00Z"},
                    },
                ],
                "nextPageToken": "next token",
            }
        return {
            "timeZone": "UTC",
            "items": [
                {
                    "id": "all-day",
                    "start": {"date": "2026-07-15"},
                    "end": {"date": "2026-07-16"},
                },
                {
                    "id": "deleted",
                    "status": "cancelled",
                    "start": {"dateTime": "2026-07-15T14:00:00Z"},
                    "end": {"dateTime": "2026-07-15T15:00:00Z"},
                },
            ],
        }

    monkeypatch.setattr(gcal, "_api", api)

    assert gcal._busy_events(start, end) == [
        ("timed", start + dt.timedelta(hours=10), start + dt.timedelta(hours=11)),
        ("all-day", start, end),
    ]
    assert len(paths) == 2
    assert "singleEvents=true" in paths[0]
    assert "pageToken=next+token" in paths[1]


def test_source_and_event_state_fail_closed(owner):
    event_id = _seed_event()
    other_event_id = _seed_event()
    source_id = _seed_booking(event_id)
    cancelled_id = _seed_booking(event_id, status="cancelled")

    missing_event = _slots(owner, 999_999, "2026-07-15")
    missing_source = _slots(owner, event_id, "2026-07-15", 999_999)
    mismatch = _slots(owner, other_event_id, "2026-07-15", source_id)
    cancelled = _slots(owner, event_id, "2026-07-15", cancelled_id)
    db.run("UPDATE event_types SET active=0 WHERE id=?", (event_id,))
    inactive = _slots(owner, event_id, "2026-07-15", source_id)

    assert (missing_event.status_code, missing_event.json()["code"]) == (
        404,
        "event_type.not_found",
    )
    assert (missing_source.status_code, missing_source.json()["code"]) == (
        404,
        "booking.not_found",
    )
    assert (mismatch.status_code, mismatch.json()["code"]) == (
        409,
        "booking.event_mismatch",
    )
    assert (cancelled.status_code, cancelled.json()["code"]) == (
        409,
        "booking.not_reschedulable",
    )
    assert (inactive.status_code, inactive.json()["code"]) == (
        409,
        "booking.event_unavailable",
    )


def test_slot_read_requires_exact_owner_with_read_scope(owner, monkeypatch):
    event_id = _seed_event()
    missing = owner["client"].get(
        f"/api/v1/event-types/{event_id}/slots",
        params={"day": "2026-07-15"},
    )
    assert missing.status_code == 401

    session = db.one("SELECT * FROM api_sessions ORDER BY created_at DESC LIMIT 1")
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
    allowed = _slots(owner, event_id, "2026-07-15")
    assert allowed.status_code == 200

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
        created_at=_NOW,
        absolute_expires_at=_NOW + dt.timedelta(days=1),
    )
    monkeypatch.setattr(mobile_auth, "authenticate_request", lambda *a, **k: guest)
    refused = _slots(owner, event_id, "2026-07-15")
    assert refused.status_code == 403
    assert refused.json()["code"] == "auth.insufficient_scope"


def test_slot_query_validation_is_strict(owner, monkeypatch):
    event_id = _seed_event()

    bad_day = _slots(owner, event_id, "07/15/2026")
    bad_event = owner["client"].get(
        "/api/v1/event-types/0/slots",
        headers=owner["headers"],
        params={"day": "2026-07-15"},
    )
    bad_source = owner["client"].get(
        f"/api/v1/event-types/{event_id}/slots",
        headers=owner["headers"],
        params={"day": "2026-07-15", "reschedule_booking_id": 0},
    )
    huge_event = owner["client"].get(
        f"/api/v1/event-types/{2**63}/slots",
        headers=owner["headers"],
        params={"day": "2026-07-15"},
    )
    huge_source = owner["client"].get(
        f"/api/v1/event-types/{event_id}/slots",
        headers=owner["headers"],
        params={"day": "2026-07-15", "reschedule_booking_id": 2**63},
    )
    monkeypatch.setattr(
        scheduling.gcal,
        "free_busy",
        lambda *_: pytest.fail("out-of-window days must not query Google"),
    )
    maximum_day = _slots(owner, event_id, "9999-12-31")

    assert bad_day.status_code == 422
    assert bad_event.status_code == 422
    assert bad_source.status_code == 422
    assert huge_event.status_code == 422
    assert huge_source.status_code == 422
    assert maximum_day.status_code == 200
    assert maximum_day.json()["slots"] == []
    assert bad_day.json()["code"] == "request.validation_failed"


def test_dst_fold_serializes_distinct_utc_instants(owner, monkeypatch):
    monkeypatch.setattr(config, "TIMEZONE", "America/New_York")
    monkeypatch.setattr(
        scheduling,
        "now_utc",
        lambda: dt.datetime(2026, 10, 25, 12, 0, tzinfo=dt.UTC),
    )
    free_busy_bounds: list[tuple[dt.datetime, dt.datetime]] = []

    def free_busy(start: dt.datetime, end: dt.datetime):
        free_busy_bounds.append((start, end))
        return None

    monkeypatch.setattr(scheduling.gcal, "free_busy", free_busy)
    event_id = _seed_event(start_min=60, end_min=4 * 60)

    response = _slots(owner, event_id, "2026-11-01")

    assert response.status_code == 200
    assert response.json()["time_zone"] == "America/New_York"
    assert [slot["start_at"] for slot in response.json()["slots"]] == [
        "2026-11-01T05:00:00Z",
        "2026-11-01T06:00:00Z",
        "2026-11-01T07:00:00Z",
        "2026-11-01T08:00:00Z",
    ]
    assert free_busy_bounds == [
        (
            dt.datetime(2026, 11, 1, 4, 0, tzinfo=dt.UTC),
            dt.datetime(2026, 11, 2, 5, 0, tzinfo=dt.UTC),
        )
    ]
