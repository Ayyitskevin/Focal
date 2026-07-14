"""Provider identity reconciliation for durable booking reschedules."""

import urllib.error

import pytest

from app import config, db, features, gcal, notion_sync

pytestmark = pytest.mark.unit

_SOURCE_START = "2026-07-15 14:00:00"
_SOURCE_END = "2026-07-15 15:00:00"
_REPLACEMENT_START = "2026-07-16 16:30:00"
_REPLACEMENT_END = "2026-07-16 17:30:00"


@pytest.fixture
def booking_pair(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "mise.db")
    monkeypatch.setattr(config, "BASE_URL", "https://studio.test")
    monkeypatch.setattr(config, "GOOGLE_CALENDAR_ID", "business-calendar@example.test")
    db.migrate()

    event_type_id = db.run(
        """INSERT INTO event_types
           (slug,name,description,duration_min,location,color,buffer_before_min,
            buffer_after_min,min_notice_hours,max_per_day,booking_window_days,
            slot_step_min,active,position,creates_notion_session)
           VALUES ('portrait','Portrait session','',60,'North Star Studio',
                   '#123ABC',0,0,0,0,90,60,1,1,1)"""
    )
    source_id = db.run(
        """INSERT INTO bookings
           (token,event_type_id,name,email,phone,notes,start_utc,end_utc,tz,status,
            cancel_reason,cancelled_at,google_event_id,notion_page_id,
            notion_session_id)
           VALUES ('old-token',?,'Alex Rivera','alex@example.test','555-0142','',
                   ?,?,'America/New_York','cancelled','Rescheduled',datetime('now'),
                   'google-existing','notion-booking-existing',
                   'notion-session-existing')""",
        (event_type_id, _SOURCE_START, _SOURCE_END),
    )
    replacement_id = db.run(
        """INSERT INTO bookings
           (token,event_type_id,name,email,phone,notes,start_utc,end_utc,tz,status,
            reschedule_of)
           VALUES ('new-token',?,'Alex Rivera','alex@example.test','555-0142','',
                   ?,?,'America/New_York','confirmed',?)""",
        (event_type_id, _REPLACEMENT_START, _REPLACEMENT_END, source_id),
    )
    return source_id, replacement_id


def _enable_gcal(monkeypatch):
    monkeypatch.setattr(gcal, "configured", lambda: True)
    monkeypatch.setattr(gcal, "is_connected", lambda: True)


def test_google_reschedule_event_id_is_scoped_to_tenant_origin(monkeypatch):
    origin = {"value": "https://first.example.test"}
    monkeypatch.setattr(gcal.urls, "public_base_url", lambda: origin["value"])

    first = gcal._reschedule_event_id(12, 34)
    assert first == gcal._reschedule_event_id(12, 34)

    origin["value"] = "https://second.example.test"
    assert gcal._reschedule_event_id(12, 34) != first


def _booking_refs(source_id, replacement_id):
    columns = "google_event_id, notion_page_id, notion_session_id"
    source = db.one(f"SELECT {columns} FROM bookings WHERE id=?", (source_id,))
    replacement = db.one(f"SELECT {columns} FROM bookings WHERE id=?", (replacement_id,))
    return source, replacement


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://provider.test", code, "provider error", {}, None)


def test_delete_transient_failure_preserves_google_pointer(booking_pair, monkeypatch):
    source_id, replacement_id = booking_pair
    monkeypatch.setattr(
        gcal,
        "_api",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("calendar timeout")),
    )

    gcal._delete_event(source_id)

    source, replacement = _booking_refs(source_id, replacement_id)
    assert source["google_event_id"] == "google-existing"
    assert replacement["google_event_id"] is None


def test_google_reschedule_moves_pointer_and_retries_with_patch(booking_pair, monkeypatch):
    source_id, replacement_id = booking_pair
    _enable_gcal(monkeypatch)
    calls = []

    def api(method, path, payload=None):
        calls.append((method, path, payload))
        return {"id": "google-existing"}

    monkeypatch.setattr(gcal, "_api", api)

    assert gcal.on_booking_rescheduled(source_id, replacement_id, strict=True) == "google-existing"
    assert gcal.on_booking_rescheduled(source_id, replacement_id, strict=True) == "google-existing"

    source, replacement = _booking_refs(source_id, replacement_id)
    assert source["google_event_id"] is None
    assert replacement["google_event_id"] == "google-existing"
    assert [call[0] for call in calls] == ["PATCH", "PATCH"]
    assert len({call[1] for call in calls}) == 1
    assert calls[0][2]["start"] == {"dateTime": "2026-07-16T16:30:00Z"}
    assert calls[0][2]["end"] == {"dateTime": "2026-07-16T17:30:00Z"}
    assert "Manage: https://studio.test/booking/new-token" in calls[0][2]["description"]


def test_google_strict_transient_failure_keeps_transferred_pointer(
    booking_pair,
    monkeypatch,
):
    source_id, replacement_id = booking_pair
    _enable_gcal(monkeypatch)
    monkeypatch.setattr(
        gcal,
        "_api",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("calendar timeout")),
    )

    with pytest.raises(TimeoutError, match="calendar timeout"):
        gcal.on_booking_rescheduled(source_id, replacement_id, strict=True)

    source, replacement = _booking_refs(source_id, replacement_id)
    assert source["google_event_id"] is None
    assert replacement["google_event_id"] == "google-existing"


@pytest.mark.parametrize("missing_status", [404, 410])
def test_google_missing_event_uses_stable_id_after_lost_create_response(
    booking_pair,
    monkeypatch,
    missing_status,
):
    source_id, replacement_id = booking_pair
    _enable_gcal(monkeypatch)
    expected_id = gcal._reschedule_event_id(source_id, replacement_id)
    calls = []
    post_count = 0

    def api(method, path, payload=None):
        nonlocal post_count
        calls.append((method, path, payload))
        if method == "PATCH" and path.endswith("/google-existing"):
            raise _http_error(missing_status)
        if method == "POST":
            post_count += 1
            assert payload["id"] == expected_id
            raise TimeoutError("create response lost")
        assert method == "PATCH" and path.endswith(f"/{expected_id}")
        return {"id": expected_id}

    monkeypatch.setattr(gcal, "_api", api)

    with pytest.raises(TimeoutError, match="create response lost"):
        gcal.on_booking_rescheduled(source_id, replacement_id, strict=True)

    source, replacement = _booking_refs(source_id, replacement_id)
    assert source["google_event_id"] is None
    assert replacement["google_event_id"] == expected_id

    assert gcal.on_booking_rescheduled(source_id, replacement_id, strict=True) == expected_id
    assert post_count == 1
    assert [call[0] for call in calls] == ["PATCH", "POST", "PATCH"]


def test_disabled_reschedule_providers_return_none_without_loading_rows(
    booking_pair,
    monkeypatch,
):
    monkeypatch.setattr(gcal, "configured", lambda: False)
    monkeypatch.setattr(features, "notion_bookings_enabled", lambda: False)
    monkeypatch.setattr(features, "notion_sessions_enabled", lambda: False)

    assert gcal.on_booking_rescheduled(-1, -2, strict=True) is None
    assert notion_sync.reschedule_booking(-1, -2) is None
    assert notion_sync.reschedule_session(-1, -2) is None


def test_notion_booking_reschedule_reuses_page_without_create(booking_pair, monkeypatch):
    source_id, replacement_id = booking_pair
    monkeypatch.setattr(features, "notion_bookings_enabled", lambda: True)
    monkeypatch.setattr(
        notion_sync,
        "_create_page",
        lambda *args, **kwargs: pytest.fail("reschedule must not create a Notion page"),
    )
    patches = []
    monkeypatch.setattr(
        notion_sync,
        "_patch_page",
        lambda page_id, props: patches.append((page_id, props)),
    )

    assert notion_sync.reschedule_booking(source_id, replacement_id) == "notion-booking-existing"
    assert notion_sync.reschedule_booking(source_id, replacement_id) == "notion-booking-existing"

    source, replacement = _booking_refs(source_id, replacement_id)
    assert source["notion_page_id"] is None
    assert replacement["notion_page_id"] == "notion-booking-existing"
    assert [page_id for page_id, _ in patches] == [
        "notion-booking-existing",
        "notion-booking-existing",
    ]
    assert patches[0][1] == {
        "When": {
            "date": {
                "start": "2026-07-16T16:30:00Z",
                "end": "2026-07-16T17:30:00Z",
            }
        },
        "Status": {"select": {"name": "Confirmed"}},
    }


def test_notion_booking_failure_keeps_page_on_replacement(booking_pair, monkeypatch):
    source_id, replacement_id = booking_pair
    monkeypatch.setattr(features, "notion_bookings_enabled", lambda: True)
    monkeypatch.setattr(
        notion_sync,
        "_patch_page",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("notion timeout")),
    )

    with pytest.raises(TimeoutError, match="notion timeout"):
        notion_sync.reschedule_booking(source_id, replacement_id)

    source, replacement = _booking_refs(source_id, replacement_id)
    assert source["notion_page_id"] is None
    assert replacement["notion_page_id"] == "notion-booking-existing"


def test_notion_session_reschedule_reuses_shared_session_without_create(
    booking_pair,
    monkeypatch,
):
    source_id, replacement_id = booking_pair
    monkeypatch.setattr(features, "notion_sessions_enabled", lambda: True)
    monkeypatch.setattr(
        notion_sync,
        "_create_page",
        lambda *args, **kwargs: pytest.fail("reschedule must not create a Notion session"),
    )
    patches = []
    monkeypatch.setattr(
        notion_sync,
        "_patch_page",
        lambda page_id, props: patches.append((page_id, props)),
    )

    assert notion_sync.reschedule_session(source_id, replacement_id) == "notion-session-existing"
    assert notion_sync.reschedule_session(source_id, replacement_id) == "notion-session-existing"

    source, replacement = _booking_refs(source_id, replacement_id)
    assert source["notion_session_id"] == "notion-session-existing"
    assert replacement["notion_session_id"] == "notion-session-existing"
    assert patches == [
        (
            "notion-session-existing",
            {"Shoot Date": {"date": {"start": "2026-07-16T16:30:00Z"}}},
        ),
        (
            "notion-session-existing",
            {"Shoot Date": {"date": {"start": "2026-07-16T16:30:00Z"}}},
        ),
    ]
