"""Strict, retryable notification effects for the durable reschedule workflow."""

import re

import pytest

from app import (
    booking_notify,
    booking_workflow,
    config,
    db,
    features,
    gcal,
    mailer,
    notion_sync,
    urls,
)
from app.booking_workflow import NotApplicable

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
    monkeypatch.setattr(config, "TIMEZONE", "UTC")
    monkeypatch.setattr(config, "SITE_NAME", "North Star Studio")
    monkeypatch.setattr(config, "GMAIL_USER", "studio@example.test")
    monkeypatch.setattr(config, "GMAIL_APP_PASSWORD", "app-password")
    db.migrate()

    event_id = db.run(
        """INSERT INTO event_types
           (slug,name,description,duration_min,location,color,buffer_before_min,
            buffer_after_min,min_notice_hours,max_per_day,booking_window_days,
            slot_step_min,active,position,creates_notion_session)
           VALUES ('portrait','Portrait session','Bring your favorite jacket.',60,
                   'North Star Studio','#123ABC',0,0,0,0,90,60,1,1,1)"""
    )
    source_id = db.run(
        """INSERT INTO bookings
           (token,event_type_id,name,email,phone,notes,start_utc,end_utc,tz,status,
            cancel_reason,cancelled_at)
           VALUES ('old-token',?,'Alex Rivera','alex@example.test','555-0142','',
                   ?,?,'America/New_York','cancelled',
                   'Rescheduled from the studio app',datetime('now'))""",
        (event_id, _SOURCE_START, _SOURCE_END),
    )
    replacement_id = db.run(
        """INSERT INTO bookings
           (token,event_type_id,name,email,phone,notes,start_utc,end_utc,tz,status,
            reschedule_of)
           VALUES ('new-token',?,'Alex Rivera','alex@example.test','555-0142','',
                   ?,?,'America/New_York','confirmed',?)""",
        (event_id, _REPLACEMENT_START, _REPLACEMENT_END, source_id),
    )
    return source_id, replacement_id


def _capture_mail(monkeypatch):
    sent = []

    def capture(*args, **kwargs):
        sent.append((args, kwargs))

    monkeypatch.setattr(mailer, "send", capture)
    return sent


def _calendar_lines(message) -> set[str]:
    return set(message["content"].splitlines())


def test_effect_order_places_old_cancel_before_replacement_request():
    assert booking_notify.RESCHEDULE_EFFECT_KINDS == (
        "client_cancel_ics",
        "client_request_ics",
        "studio_reschedule_notice",
        "notion_booking_patch",
        "notion_session_link",
        "google_calendar_move",
    )
    assert booking_notify.RESCHEDULE_EFFECT_KINDS == tuple(
        kind for kind, _sequence in booking_workflow.EFFECT_KINDS
    )


def test_client_calendar_effects_have_exact_old_cancel_new_request_identity(
    booking_pair,
    monkeypatch,
):
    source_id, replacement_id = booking_pair
    sent = _capture_mail(monkeypatch)

    cancel_ref = booking_notify.run_reschedule_effect(
        booking_notify.RESCHEDULE_CLIENT_CANCEL,
        source_id,
        replacement_id,
    )
    request_ref = booking_notify.run_reschedule_effect(
        booking_notify.RESCHEDULE_CLIENT_REQUEST,
        source_id,
        replacement_id,
    )

    assert len(sent) == 2
    cancel_args, cancel_kwargs = sent[0]
    request_args, request_kwargs = sent[1]
    assert cancel_ref == cancel_kwargs["message_id"]
    assert request_ref == request_kwargs["message_id"]
    assert cancel_ref != request_ref
    assert cancel_args[0] == request_args[0] == "alex@example.test"
    assert "Previous booking time cancelled" in cancel_args[1]
    assert "Booking rescheduled" in request_args[1]

    cancel = cancel_kwargs["ics"]
    request = request_kwargs["ics"]
    assert cancel["method"] == "CANCEL"
    assert cancel["filename"] == "previous-time-cancelled.ics"
    assert {
        "METHOD:CANCEL",
        f"UID:mise-booking-{source_id}@kleephotography.com",
        "SEQUENCE:1",
        "DTSTART:20260715T140000Z",
        "DTEND:20260715T150000Z",
        "STATUS:CANCELLED",
    } <= _calendar_lines(cancel)
    assert request["method"] == "REQUEST"
    assert request["filename"] == "rescheduled-invite.ics"
    assert {
        "METHOD:REQUEST",
        f"UID:mise-booking-{replacement_id}@kleephotography.com",
        "SEQUENCE:0",
        "DTSTART:20260716T163000Z",
        "DTEND:20260716T173000Z",
        "STATUS:CONFIRMED",
    } <= _calendar_lines(request)
    assert "old-token" not in request["content"]
    assert "https://studio.test/booking/new-token" in request["content"]

    cancel_mime = mailer._build_message(
        *cancel_args,
        reply_to=cancel_kwargs["reply_to"],
        ics=cancel,
        message_id=cancel_kwargs["message_id"],
    )
    request_mime = mailer._build_message(
        *request_args,
        reply_to=request_kwargs["reply_to"],
        ics=request,
        message_id=request_kwargs["message_id"],
    )
    assert cancel_mime["Message-ID"] == cancel_ref
    assert request_mime["Message-ID"] == request_ref
    cancel_part = next(cancel_mime.iter_attachments())
    request_part = next(request_mime.iter_attachments())
    assert cancel_part.get_content_type() == request_part.get_content_type() == "text/calendar"
    assert cancel_part.get_param("method") == "CANCEL"
    assert request_part.get_param("method") == "REQUEST"
    assert cancel_part.get_filename() == "previous-time-cancelled.ics"
    assert request_part.get_filename() == "rescheduled-invite.ics"
    assert "METHOD:CANCEL" in cancel_part.get_content()
    assert "METHOD:REQUEST" in request_part.get_content()


def test_studio_notice_is_one_calendar_free_message(booking_pair, monkeypatch):
    source_id, replacement_id = booking_pair
    sent = _capture_mail(monkeypatch)

    provider_ref = booking_notify.run_reschedule_effect(
        booking_notify.RESCHEDULE_STUDIO_NOTICE,
        source_id,
        replacement_id,
    )

    assert len(sent) == 1
    args, kwargs = sent[0]
    assert args[0] == "studio@example.test"
    assert "Booking RESCHEDULED" in args[1]
    assert "Previous:" in args[2] and "New:" in args[2]
    assert kwargs["reply_to"] == "alex@example.test"
    assert "ics" not in kwargs
    assert provider_ref == kwargs["message_id"]


def test_message_identity_is_stable_tenant_scoped_and_contains_no_contact_data(
    booking_pair,
    monkeypatch,
):
    source_id, replacement_id = booking_pair
    message_id = booking_notify._reschedule_message_id(
        source_id,
        replacement_id,
        booking_notify.RESCHEDULE_CLIENT_CANCEL,
    )
    repeated = booking_notify._reschedule_message_id(
        source_id,
        replacement_id,
        booking_notify.RESCHEDULE_CLIENT_CANCEL,
    )
    monkeypatch.setattr(urls, "public_base_url", lambda: "https://another-studio.test")
    other_tenant = booking_notify._reschedule_message_id(
        source_id,
        replacement_id,
        booking_notify.RESCHEDULE_CLIENT_CANCEL,
    )

    assert message_id == repeated
    assert message_id != other_tenant
    assert re.fullmatch(
        r"<mise-reschedule-[0-9a-f]{64}@kleephotography[.]com>",
        message_id,
    )
    assert "alex" not in message_id.lower()


def test_unconfigured_mail_is_not_applicable(booking_pair, monkeypatch):
    source_id, replacement_id = booking_pair
    monkeypatch.setattr(mailer, "configured", lambda: False)

    with pytest.raises(NotApplicable):
        booking_notify.run_reschedule_effect(
            booking_notify.RESCHEDULE_CLIENT_CANCEL,
            source_id,
            replacement_id,
        )


def test_mail_transport_failure_escapes_for_retry(booking_pair, monkeypatch):
    source_id, replacement_id = booking_pair
    monkeypatch.setattr(
        mailer,
        "send",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("smtp unavailable")),
    )

    with pytest.raises(RuntimeError, match="smtp unavailable"):
        booking_notify.run_reschedule_effect(
            booking_notify.RESCHEDULE_CLIENT_REQUEST,
            source_id,
            replacement_id,
        )


def test_non_mail_effects_use_strict_provider_entrypoints(booking_pair, monkeypatch):
    source_id, replacement_id = booking_pair
    calls = []
    monkeypatch.setattr(features, "notion_bookings_enabled", lambda: True)
    monkeypatch.setattr(features, "notion_sessions_enabled", lambda: True)
    monkeypatch.setattr(gcal, "configured", lambda: True)
    monkeypatch.setattr(gcal, "is_connected", lambda: True)
    monkeypatch.setattr(
        notion_sync,
        "reschedule_booking",
        lambda source, replacement: (
            calls.append(("notion_booking_patch", source, replacement)) or "notion-booking-page"
        ),
    )
    monkeypatch.setattr(
        notion_sync,
        "reschedule_session",
        lambda source, replacement: (
            calls.append(("notion_session_link", source, replacement)) or "notion-session-page"
        ),
    )

    def reschedule_google(source, replacement, *, strict):
        calls.append(("google_calendar_move", source, replacement, strict))
        return "google-event"

    monkeypatch.setattr(gcal, "on_booking_rescheduled", reschedule_google)

    assert (
        booking_notify.run_reschedule_effect(
            booking_notify.RESCHEDULE_NOTION_BOOKING,
            source_id,
            replacement_id,
        )
        == "notion-booking-page"
    )
    assert (
        booking_notify.run_reschedule_effect(
            booking_notify.RESCHEDULE_NOTION_SESSION,
            source_id,
            replacement_id,
        )
        == "notion-session-page"
    )
    assert (
        booking_notify.run_reschedule_effect(
            booking_notify.RESCHEDULE_GOOGLE_CALENDAR,
            source_id,
            replacement_id,
        )
        == "google-event"
    )
    assert calls == [
        ("notion_booking_patch", source_id, replacement_id),
        ("notion_session_link", source_id, replacement_id),
        ("google_calendar_move", source_id, replacement_id, True),
    ]


def test_disabled_non_mail_providers_are_not_applicable(booking_pair, monkeypatch):
    source_id, replacement_id = booking_pair
    monkeypatch.setattr(features, "notion_bookings_enabled", lambda: False)
    monkeypatch.setattr(features, "notion_sessions_enabled", lambda: False)
    monkeypatch.setattr(gcal, "configured", lambda: False)

    for kind in (
        booking_notify.RESCHEDULE_NOTION_BOOKING,
        booking_notify.RESCHEDULE_NOTION_SESSION,
        booking_notify.RESCHEDULE_GOOGLE_CALENDAR,
    ):
        with pytest.raises(NotApplicable):
            booking_notify.run_reschedule_effect(kind, source_id, replacement_id)


def test_enabled_provider_without_existing_object_is_not_applicable(
    booking_pair,
    monkeypatch,
):
    source_id, replacement_id = booking_pair
    monkeypatch.setattr(features, "notion_bookings_enabled", lambda: True)
    monkeypatch.setattr(notion_sync, "reschedule_booking", lambda *_args: None)

    with pytest.raises(NotApplicable, match="no existing Notion booking page"):
        booking_notify.run_reschedule_effect(
            booking_notify.RESCHEDULE_NOTION_BOOKING,
            source_id,
            replacement_id,
        )


def test_unknown_effect_and_mismatched_pair_fail_closed(booking_pair):
    source_id, replacement_id = booking_pair
    with pytest.raises(ValueError, match="unknown"):
        booking_notify.run_reschedule_effect("send_everything", source_id, replacement_id)
    with pytest.raises(ValueError, match="not the replacement"):
        booking_notify.run_reschedule_effect(
            booking_notify.RESCHEDULE_CLIENT_CANCEL,
            replacement_id,
            source_id,
        )
