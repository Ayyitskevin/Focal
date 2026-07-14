"""Side-effects of a booking: email/calendar notices and provider writebacks.

Kept separate from the routes so the public/admin handlers stay thin and the
"what happens when a booking is made" story lives in one place. Legacy confirm
and cancel effects remain best-effort after commit. Durable reschedule effects
are strict: failures escape to their retrying workflow instead of being swallowed.
"""

import datetime as dt
import hashlib
import logging
from zoneinfo import ZoneInfo

from . import config, db, features, gcal, ics, mailer, notion_sync, urls
from .booking_workflow import NotApplicable

log = logging.getLogger("mise.booking")
_UTC = dt.UTC

RESCHEDULE_CLIENT_CANCEL = "client_cancel_ics"
RESCHEDULE_CLIENT_REQUEST = "client_request_ics"
RESCHEDULE_STUDIO_NOTICE = "studio_reschedule_notice"
RESCHEDULE_NOTION_BOOKING = "notion_booking_patch"
RESCHEDULE_NOTION_SESSION = "notion_session_link"
RESCHEDULE_GOOGLE_CALENDAR = "google_calendar_move"

# This order is part of the effect contract. In particular, the old calendar UID
# must be cancelled before the replacement UID is requested.
RESCHEDULE_EFFECT_KINDS = (
    RESCHEDULE_CLIENT_CANCEL,
    RESCHEDULE_CLIENT_REQUEST,
    RESCHEDULE_STUDIO_NOTICE,
    RESCHEDULE_NOTION_BOOKING,
    RESCHEDULE_NOTION_SESSION,
    RESCHEDULE_GOOGLE_CALENDAR,
)


def _load(booking_id: int):
    return db.one(
        """SELECT b.*, e.name AS event_name, e.location, e.description AS event_desc
           FROM bookings b JOIN event_types e ON e.id=b.event_type_id
           WHERE b.id=?""",
        (booking_id,),
    )


def _when(start_utc: str, tzname: str) -> str:
    d = dt.datetime.strptime(start_utc, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_UTC)
    try:
        local = d.astimezone(ZoneInfo(tzname or config.TIMEZONE))
    except Exception:
        local = d.astimezone(ZoneInfo(config.TIMEZONE))
    return local.strftime("%A, %B %-d, %Y · %-I:%M %p %Z").replace(" 0", " ")


def _manage_url(token: str) -> str:
    # Tenant-host aware: a studio's client must land on the studio's own origin.
    return f"{urls.public_base_url()}/booking/{token}"


def _link_studio(booking_id: int, inquiry_id: int | None) -> None:
    """Find-or-create the Studio client (always) and, for real-shoot event types,
    a project — so the booking, inquiry, client, project and Notion Session share one
    identity instead of spawning duplicate leads. Best-effort: the booking is already
    committed, so a CRM hiccup here must never lose it (fail loud, not lost).

    Runs before the Notion Session sync so the session-create branch can stamp the
    new project's notion_page_id, unifying project <-> Session for the pipeline."""
    b = db.one(
        """SELECT b.*, e.name AS event_name, e.creates_notion_session
                  FROM bookings b JOIN event_types e ON e.id=b.event_type_id
                  WHERE b.id=?""",
        (booking_id,),
    )
    if not b or b["client_id"]:  # idempotent on re-run
        return

    # A reschedule inherits the original booking's client/project — never a new lead.
    if b["reschedule_of"]:
        prev = db.one(
            "SELECT client_id, project_id FROM bookings WHERE id=?", (b["reschedule_of"],)
        )
        if prev and prev["client_id"]:
            db.run(
                "UPDATE bookings SET client_id=?, project_id=? WHERE id=?",
                (prev["client_id"], prev["project_id"], booking_id),
            )
            return

    existing = db.one("SELECT id FROM clients WHERE email=?", (b["email"],))
    cid = (
        existing["id"]
        if existing
        else db.run(
            "INSERT INTO clients (name, email, phone, notes) VALUES (?,?,?,?)",
            (
                b["name"],
                b["email"],
                b["phone"] or "",
                f"Auto-created from a Mise booking {b['created_at'][:10]}.",
            ),
        )
    )
    if not existing:
        log.info("booking %s created client %s", booking_id, cid)

    pid = None
    # Only real shoots (the same opt-in that spawns a Notion Session) get a project;
    # discovery/consult calls stay client-only so Kevin promotes them by hand if needed.
    if b["creates_notion_session"]:
        title = f"{b['event_name']} — {b['start_utc'][:10]}"
        pid = db.run(
            """INSERT INTO projects (client_id, title, shoot_date, notes)
                        VALUES (?,?,?,?)""",
            (cid, title, b["start_utc"][:10], notion_sync.intake_summary(b) or None),
        )
        log.info("booking %s spawned project %s", booking_id, pid)
        # Fade the mirrored inquiry out of the studio 'to convert' list so Kevin's
        # manual convert button can't double-create the same project.
        if inquiry_id:
            db.run(
                """UPDATE inquiries SET converted_at=datetime('now'),
                      converted_client_id=?, converted_project_id=? WHERE id=?""",
                (cid, pid, inquiry_id),
            )

    db.run("UPDATE bookings SET client_id=?, project_id=? WHERE id=?", (cid, pid, booking_id))


def confirm(booking_id: int) -> None:
    """Email client + Kevin with the invite; mirror to Odysseus + Notion."""
    b = _load(booking_id)
    if not b:
        log.error("confirm: booking %s vanished", booking_id)
        return
    biz_when = _when(b["start_utc"], config.TIMEZONE)
    cli_when = _when(b["start_utc"], b["tz"])
    uid = ics.uid_for(booking_id, b["calendar_uid"])
    loc = b["location"] or "Details to follow"
    summary = f"{b['event_name']} · {mailer.sender_name()}"
    details = (
        f"{b['event_desc']}\n\n" if b["event_desc"] else ""
    ) + f"Manage this booking: {_manage_url(b['token'])}"
    gcal_link = ics.google_link(
        summary=summary,
        details=details,
        location=loc,
        start_utc=b["start_utc"],
        end_utc=b["end_utc"],
    )

    if not mailer.configured():
        log.error("booking %s confirmed but mailer not configured — no emails sent", booking_id)
    else:
        invite = {
            "filename": "invite.ics",
            "method": "REQUEST",
            "content": ics.build(
                uid=uid,
                summary=summary,
                description=details,
                location=loc,
                start_utc=b["start_utc"],
                end_utc=b["end_utc"],
                organizer_email=config.GMAIL_USER,
                attendee_email=b["email"],
            ),
        }
        client_body = (
            f"Hi {b['name']},\n\n"
            f"Your booking is confirmed:\n\n"
            f"  {b['event_name']}\n  {cli_when}\n  {loc}\n\n"
            f"Add it to your calendar with the attached invite, or here:\n{gcal_link}\n\n"
            f"Need to change or cancel? {_manage_url(b['token'])}\n\n"
            f"— {mailer.sender_name()}\n"
        )
        kevin_body = (
            f"New booking via {urls.public_base_url()}\n\n"
            f"Event: {b['event_name']}\nWhen: {biz_when}\n"
            f"Name: {b['name']}\nEmail: {b['email']}\nPhone: {b['phone'] or '—'}\n\n"
            f"{b['notes'] or '(no note)'}\n\nManage: {_manage_url(b['token'])}\n"
        )
        try:
            mailer.send(
                b["email"],
                f"Booking confirmed — {b['event_name']}",
                client_body,
                reply_to=mailer.studio_inbox(),
                ics=invite,
            )
        except Exception as e:
            log.error("booking %s client email failed: %s", booking_id, e)
        try:
            # Kevin's copy doubles as the Odysseus inquiry_intake hook (it polls his inbox).
            mailer.send(
                mailer.studio_inbox(),
                f"Booking — {b['name']} · {b['event_name']} · {biz_when}",
                kevin_body,
                reply_to=b["email"],
                ics=invite,
            )
        except Exception as e:
            log.error("booking %s kevin email failed: %s", booking_id, e)

    # Mise-side inquiry row keeps the admin inquiry list + Odysseus consistent.
    iid = None
    try:
        iid = db.run(
            """INSERT INTO inquiries (name, email, business, message, kind,
                                      shoot_date, service, emailed)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                b["name"],
                b["email"],
                None,
                f"Booked {b['event_name']} for {biz_when}.\n\n{b['notes']}",
                "booking",
                b["start_utc"][:10],
                b["event_name"],
                1 if mailer.configured() else 0,
            ),
        )
        db.run("UPDATE bookings SET inquiry_id=? WHERE id=?", (iid, booking_id))
    except Exception as e:
        log.error("booking %s inquiry-row mirror failed: %s", booking_id, e)

    # Link the booking into the Studio CRM (client always; project for real shoots).
    try:
        _link_studio(booking_id, iid)
    except Exception as e:
        log.error("booking %s studio link failed: %s", booking_id, e)

    try:
        notion_sync.sync_booking(booking_id)
    except Exception as e:
        log.error("booking %s notion writeback failed: %s", booking_id, e)

    # Seed/link the Notion Session spine (no-op unless the event type opted in
    # and NOTION_SESSIONS_DB is armed). Kept separate from the calendar-mirror
    # writeback above — different gate, different failure domain.
    try:
        notion_sync.sync_session_for_booking(booking_id)
    except Exception as e:
        log.error("booking %s notion session sync failed: %s", booking_id, e)

    # Mirror onto Kevin's Google calendar (best-effort; no-op if not connected).
    gcal.on_booking_confirmed(booking_id)


def cancelled(booking_id: int, by_admin: bool = False) -> None:
    """Email both parties a CANCEL invite so the held slot drops off calendars."""
    b = _load(booking_id)
    if not b:
        return
    cli_when = _when(b["start_utc"], b["tz"])
    summary = f"{b['event_name']} · {mailer.sender_name()}"
    if mailer.configured():
        cancel_ics = {
            "filename": "cancel.ics",
            "method": "CANCEL",
            "content": ics.build(
                uid=ics.uid_for(booking_id, b["calendar_uid"]),
                summary=summary,
                description="This booking was cancelled.",
                location=b["location"] or "",
                start_utc=b["start_utc"],
                end_utc=b["end_utc"],
                organizer_email=config.GMAIL_USER,
                attendee_email=b["email"],
                sequence=1,
                cancelled=True,
            ),
        }
        body = (
            f"Hi {b['name']},\n\nYour booking has been cancelled:\n\n"
            f"  {b['event_name']}\n  {cli_when}\n\n"
            f"Book a new time any time: {urls.public_base_url()}/book\n\n"
            f"— {mailer.sender_name()}\n"
        )
        try:
            mailer.send(
                b["email"],
                f"Booking cancelled — {b['event_name']}",
                body,
                reply_to=mailer.studio_inbox(),
                ics=cancel_ics,
            )
        except Exception as e:
            log.error("booking %s cancel email failed: %s", booking_id, e)
        if not by_admin:
            try:
                mailer.send(
                    mailer.studio_inbox(),
                    f"Booking CANCELLED — {b['name']} · {b['event_name']}",
                    f"{b['name']} cancelled their {b['event_name']} "
                    f"({_when(b['start_utc'], config.TIMEZONE)}).\n"
                    f"Reason: {b['cancel_reason'] or '—'}\n",
                    reply_to=b["email"],
                )
            except Exception as e:
                log.error("booking %s kevin cancel email failed: %s", booking_id, e)
    try:
        notion_sync.sync_booking(booking_id)
    except Exception as e:
        log.error("booking %s notion cancel writeback failed: %s", booking_id, e)

    # Drop the matching Google calendar event (best-effort; no-op if not connected).
    gcal.on_booking_cancelled(booking_id)


# ── durable reschedule effects ───────────────────────────────────────────────


def _reschedule_pair(source_id: int, replacement_id: int):
    source = _load(source_id)
    replacement = _load(replacement_id)
    if not source:
        raise ValueError(f"source booking {source_id} not found")
    if not replacement:
        raise ValueError(f"replacement booking {replacement_id} not found")
    if replacement["reschedule_of"] != source_id:
        raise ValueError(f"booking {replacement_id} is not the replacement for booking {source_id}")
    return source, replacement


def _reschedule_message_id(source_id: int, replacement_id: int, kind: str) -> str:
    """Stable, tenant-scoped SMTP identity without contact data in the header."""
    origin = urls.public_base_url().rstrip("/").lower()
    identity = (
        f"mise-reschedule-message-v1\0{origin}\0{source_id}\0{replacement_id}\0{kind}"
    ).encode()
    digest = hashlib.sha256(identity).hexdigest()
    return f"<mise-reschedule-{digest}@kleephotography.com>"


def _require_mailer() -> None:
    if not mailer.configured():
        raise NotApplicable("booking email is not configured for this studio")


def _send_reschedule_cancel(source, replacement) -> str:
    _require_mailer()
    kind = RESCHEDULE_CLIENT_CANCEL
    message_id = _reschedule_message_id(source["id"], replacement["id"], kind)
    summary = f"{source['event_name']} · {mailer.sender_name()}"
    invite = {
        "filename": "previous-time-cancelled.ics",
        "method": "CANCEL",
        "content": ics.build(
            uid=ics.uid_for(source["id"], source["calendar_uid"]),
            summary=summary,
            description="This previous booking time was cancelled because the booking moved.",
            location=source["location"] or "",
            start_utc=source["start_utc"],
            end_utc=source["end_utc"],
            organizer_email=config.GMAIL_USER,
            attendee_email=source["email"],
            sequence=1,
            cancelled=True,
        ),
    }
    follow_up = (
        "If your replacement remains active, its invitation for "
        f"{_when(replacement['start_utc'], replacement['tz'])} will arrive separately. "
        "If it is cancelled or replaced, no new invitation will follow."
    )
    body = (
        f"Hi {source['name']},\n\n"
        f"We cancelled your previous {source['event_name']} time as part of your "
        f"reschedule:\n\n  {_when(source['start_utc'], source['tz'])}\n\n"
        f"{follow_up}\n\n"
        f"— {mailer.sender_name()}\n"
    )
    mailer.send(
        source["email"],
        f"Previous booking time cancelled — {source['event_name']}",
        body,
        reply_to=mailer.studio_inbox(),
        ics=invite,
        message_id=message_id,
    )
    return message_id


def _send_reschedule_request(source, replacement) -> str:
    _require_mailer()
    kind = RESCHEDULE_CLIENT_REQUEST
    message_id = _reschedule_message_id(source["id"], replacement["id"], kind)
    summary = f"{replacement['event_name']} · {mailer.sender_name()}"
    location = replacement["location"] or "Details to follow"
    details = (
        f"{replacement['event_desc']}\n\n" if replacement["event_desc"] else ""
    ) + f"Manage this booking: {_manage_url(replacement['token'])}"
    invite = {
        "filename": "rescheduled-invite.ics",
        "method": "REQUEST",
        "content": ics.build(
            uid=ics.uid_for(replacement["id"], replacement["calendar_uid"]),
            summary=summary,
            description=details,
            location=location,
            start_utc=replacement["start_utc"],
            end_utc=replacement["end_utc"],
            organizer_email=config.GMAIL_USER,
            attendee_email=replacement["email"],
            sequence=0,
            cancelled=False,
        ),
    }
    body = (
        f"Hi {replacement['name']},\n\n"
        f"Your booking has been rescheduled:\n\n"
        f"  {replacement['event_name']}\n"
        f"  {_when(replacement['start_utc'], replacement['tz'])}\n"
        f"  {location}\n\n"
        f"Add the new time with the attached invitation.\n\n"
        f"Need to change or cancel? {_manage_url(replacement['token'])}\n\n"
        f"— {mailer.sender_name()}\n"
    )
    mailer.send(
        replacement["email"],
        f"Booking rescheduled — {replacement['event_name']}",
        body,
        reply_to=mailer.studio_inbox(),
        ics=invite,
        message_id=message_id,
    )
    return message_id


def _send_reschedule_studio_notice(source, replacement) -> str:
    _require_mailer()
    kind = RESCHEDULE_STUDIO_NOTICE
    message_id = _reschedule_message_id(source["id"], replacement["id"], kind)
    body = (
        f"{replacement['name']} rescheduled their {replacement['event_name']}.\n\n"
        f"Previous: {_when(source['start_utc'], config.TIMEZONE)}\n"
        f"New: {_when(replacement['start_utc'], config.TIMEZONE)}\n"
        f"Email: {replacement['email']}\n"
        f"Manage: {_manage_url(replacement['token'])}\n"
    )
    mailer.send(
        mailer.studio_inbox(),
        f"Booking RESCHEDULED — {replacement['name']} · {replacement['event_name']}",
        body,
        reply_to=replacement["email"],
        message_id=message_id,
    )
    return message_id


def run_reschedule_effect(
    kind: str,
    source_id: int,
    replacement_id: int,
) -> str | None:
    """Execute one strict, retryable reschedule effect.

    Provider and transport failures deliberately escape to the durable workflow.
    A provider that is not armed in the active tenant raises NotApplicable so the
    outbox can record a terminal skip instead of retrying a disabled integration.
    """
    if kind not in RESCHEDULE_EFFECT_KINDS:
        raise ValueError(f"unknown booking reschedule effect: {kind}")
    source, replacement = _reschedule_pair(source_id, replacement_id)
    if source["status"] != "cancelled":
        raise ValueError(f"source booking {source_id} is not cancelled")
    if kind != RESCHEDULE_CLIENT_CANCEL and replacement["status"] != "confirmed":
        raise NotApplicable("replacement_superseded")

    if kind == RESCHEDULE_CLIENT_CANCEL:
        return _send_reschedule_cancel(source, replacement)
    if kind == RESCHEDULE_CLIENT_REQUEST:
        return _send_reschedule_request(source, replacement)
    if kind == RESCHEDULE_STUDIO_NOTICE:
        return _send_reschedule_studio_notice(source, replacement)
    if kind == RESCHEDULE_NOTION_BOOKING:
        if not features.notion_bookings_enabled():
            raise NotApplicable("Notion booking sync is not enabled for this studio")
        provider_ref = notion_sync.reschedule_booking(source_id, replacement_id)
        if provider_ref is None:
            raise NotApplicable("this booking has no existing Notion booking page")
        return provider_ref
    if kind == RESCHEDULE_NOTION_SESSION:
        if not features.notion_sessions_enabled():
            raise NotApplicable("Notion session sync is not enabled for this studio")
        provider_ref = notion_sync.reschedule_session(source_id, replacement_id)
        if provider_ref is None:
            raise NotApplicable("this booking has no existing Notion session")
        return provider_ref
    if kind == RESCHEDULE_GOOGLE_CALENDAR:
        if not gcal.configured() or not gcal.is_connected():
            raise NotApplicable("Google Calendar is not connected for this studio")
        provider_ref = gcal.on_booking_rescheduled(source_id, replacement_id, strict=True)
        if provider_ref is None:
            raise NotApplicable("this booking is not eligible for Google Calendar sync")
        return provider_ref
    raise AssertionError("validated reschedule effect was not dispatched")
