"""Policy-sensitive native commands for owner scheduling and client proposals.

Booking management remains owner-only until Mise has a dedicated client booking
credential. Proposal decisions require the exact document response capability.
Every command reuses the session-bound replay ledger introduced in Milestone 4A.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import (
    audit,
    booking_notify,
    config,
    db,
    gcal,
    jobs,
    mobile_auth,
    push_notifications,
    scheduling,
    security,
    workflows,
)
from . import mobile_client_delivery_api as delivery
from . import mobile_owner_mutation_api as mutations
from .mobile_gallery_calendar_api import Booking

log = logging.getLogger("mise.mobile_policy_mutation_api")
router = APIRouter()
_OWNER_ACTOR = "mobile_owner"
_CLIENT_ACTOR = "mobile_client"


class PolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BookingCancelRequest(PolicyRequest):
    reason: str = Field(default="", max_length=500)

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str) -> str:
        return value.strip()


class BookingRescheduleRequest(PolicyRequest):
    start_at: dt.datetime
    time_zone: str = Field(default="", max_length=255)

    @field_validator("start_at")
    @classmethod
    def aware_start(cls, value: dt.datetime) -> dt.datetime:
        return mutations._aware_utc(value)

    @field_validator("time_zone")
    @classmethod
    def valid_time_zone(cls, value: str) -> str:
        cleaned = value.strip()
        if cleaned:
            try:
                ZoneInfo(cleaned)
            except Exception as exc:
                raise ValueError("unknown time zone") from exc
        return cleaned


class BookingSlot(mutations.MobileWriteModel):
    start_at: dt.datetime

    @field_validator("start_at")
    @classmethod
    def aware_start(cls, value: dt.datetime) -> dt.datetime:
        return mutations._aware_utc(value)


class BookingSlots(mutations.MobileWriteModel):
    day: dt.date
    time_zone: str = Field(min_length=1, max_length=255)
    items: list[BookingSlot] = Field(max_length=500)


def _booking_detail(con, booking_id: int) -> Booking:
    row = con.execute(
        """SELECT b.id, b.event_type_id, e.name AS event_name, b.name,
                  b.email, b.phone, b.notes, b.start_utc, b.end_utc, b.tz,
                  b.status, b.client_id, b.project_id, b.reschedule_of,
                  b.cancel_reason, b.cancelled_at, b.created_at
             FROM bookings b JOIN event_types e ON e.id=b.event_type_id
            WHERE b.id=?""",
        (booking_id,),
    ).fetchone()
    if row is None:
        raise mobile_auth.MobileAuthError(404, "booking.not_found", "Booking not found.")
    return Booking(
        id=int(row["id"]),
        event_type_id=int(row["event_type_id"]),
        event_name=str(row["event_name"]).strip()[:500] or "Booking",
        name=str(row["name"]).strip()[:500] or "Client",
        email=str(row["email"]).strip()[:320],
        phone=mutations._optional(row["phone"], 100),
        notes=mutations._optional(row["notes"], 10_000),
        start_at=mutations._sqlite_utc(row["start_utc"]),
        end_at=mutations._sqlite_utc(row["end_utc"]),
        time_zone=mutations._optional(row["tz"], 255) or config.TIMEZONE,
        status=str(row["status"]),
        client_id=int(row["client_id"]) if row["client_id"] is not None else None,
        project_id=int(row["project_id"]) if row["project_id"] is not None else None,
        rescheduled_from_id=(
            int(row["reschedule_of"]) if row["reschedule_of"] is not None else None
        ),
        cancel_reason=mutations._optional(row["cancel_reason"], 2000),
        cancelled_at=mutations._sqlite_utc(row["cancelled_at"]),
        created_at=mutations._sqlite_utc(row["created_at"]),
    )


def _booking_etag(value: Booking) -> str:
    digest = hashlib.sha256(value.model_dump_json().encode()).hexdigest()
    return f'"booking-{digest[:32]}"'


def _event_for_booking(con, booking_id: int):
    row = con.execute(
        """SELECT e.* FROM bookings b JOIN event_types e ON e.id=b.event_type_id
            WHERE b.id=?""",
        (booking_id,),
    ).fetchone()
    if row is None:
        raise mobile_auth.MobileAuthError(404, "booking.not_found", "Booking not found.")
    if not row["active"]:
        raise mobile_auth.MobileAuthError(
            409,
            "booking.event_unavailable",
            "This event type is no longer available for rescheduling.",
        )
    return row


def _proposal_principal(request: Request) -> mobile_auth.Principal:
    principal = delivery.require_document_guest(request)
    resource_id = principal.resource_id
    if (
        principal.resource_variant != "proposal"
        or resource_id is None
        or not principal.has_scope(f"document:proposal:{resource_id}:respond")
    ):
        raise mobile_auth.MobileAuthError(
            403,
            "auth.insufficient_scope",
            "This action requires the exact proposal response capability.",
        )
    return principal


ProposalResponder = Annotated[mobile_auth.Principal, Depends(_proposal_principal)]


def _proposal_from_row(request: Request, row, *, can_act: bool) -> delivery.DocumentDelivery:
    common = delivery._document_common(row, delivery.DocumentKind.PROPOSAL, request)
    return delivery.DocumentDelivery(
        **common,
        detail=delivery._clean_text(row["intro"], maximum=100_000),
        line_items=delivery._line_items(row["line_items"]),
        total=delivery._money(int(row["total_cents"])),
        deposit=None,
        paid=None,
        balance=None,
        payments=[],
        payment_count=0,
        payments_truncated=False,
        due_on=None,
        completed_at=delivery._utc_timestamp(row["accepted_at"]),
        document_etag=None,
        can_act=can_act and row["status"] in ("sent", "viewed"),
    )


def _document_etag(value: delivery.DocumentDelivery) -> str:
    return f'"{hashlib.sha256(value.model_dump_json().encode()).hexdigest()}"'


def _enqueue_effect(con, principal: mobile_auth.Principal, key: str) -> int:
    return jobs.enqueue_in_transaction(
        con,
        "mobile_policy_effect",
        {"session_id": principal.session_id, "idempotency_key": key},
    )


def deliver_pending_effect(session_id: str, key: str) -> None:
    """Lease and deliver one durable policy effect.

    The queue retries raised failures and startup requeues interrupted jobs. The
    lease prevents concurrent command replays/workers from dispatching together.
    """
    with mutations._immediate_transaction() as con:
        claimed = con.execute(
            """UPDATE mobile_commands
                  SET effects_claimed_at=datetime('now'),
                      effects_attempts=effects_attempts+1,
                      effects_last_error=NULL
                WHERE session_id=? AND idempotency_key=?
                  AND effect_json IS NOT NULL
                  AND effects_completed_at IS NULL
                  AND (effects_claimed_at IS NULL OR
                       effects_claimed_at < datetime('now','-5 minutes'))""",
            (session_id, key),
        )
        if claimed.rowcount != 1:
            return
        row = con.execute(
            """SELECT effect_json FROM mobile_commands
                WHERE session_id=? AND idempotency_key=?""",
            (session_id, key),
        ).fetchone()
    assert row is not None
    try:
        effect = json.loads(row["effect_json"])
        kind = effect.get("kind")
        if kind == "booking_cancel":
            booking_notify.cancelled(int(effect["booking_id"]), by_admin=True)
        elif kind == "booking_reschedule":
            booking_notify.rescheduled(int(effect["booking_id"]))
        elif kind == "proposal_accept":
            project_id = int(effect["project_id"])
            proposal_id = int(effect["proposal_id"])
            workflows.record_project_event(
                project_id,
                "proposal",
                str(effect["label"]),
                ref_kind="proposal",
                ref_id=proposal_id,
                dedupe_key=f"proposal_accepted:{proposal_id}",
            )
            workflows.fire_workflow(
                "proposal_accepted",
                project_id,
                ref_kind="proposal",
                ref_id=proposal_id,
            )
        else:
            raise ValueError("unsupported mobile policy effect")
    except Exception as exc:
        db.run(
            """UPDATE mobile_commands
                  SET effects_claimed_at=NULL, effects_last_error=?
                WHERE session_id=? AND idempotency_key=?
                  AND effects_completed_at IS NULL""",
            (str(exc)[:500], session_id, key),
        )
        raise
    db.run(
        """UPDATE mobile_commands
              SET effects_completed_at=datetime('now'),
                  effects_claimed_at=NULL,
                  effects_last_error=NULL
            WHERE session_id=? AND idempotency_key=?
              AND effects_completed_at IS NULL""",
        (session_id, key),
    )


@router.get(
    "/bookings/{booking_id}",
    response_model=Booking,
    tags=["owner scheduling commands"],
)
def booking_detail(
    request: Request,
    response: Response,
    booking_id: Annotated[int, Path(ge=1)],
    _principal: mutations.StudioWriter,
) -> Booking | Response:
    con = db.connect()
    try:
        value = _booking_detail(con, booking_id)
    finally:
        con.close()
    etag = _booking_etag(value)
    if mutations._etag_matches(request.headers.get("If-None-Match"), etag):
        return Response(status_code=304, headers={"Cache-Control": "no-store", "ETag": etag})
    mutations._private(response, etag=etag)
    return value


@router.get(
    "/bookings/{booking_id}/slots",
    response_model=BookingSlots,
    tags=["owner scheduling commands"],
)
def booking_slots(
    response: Response,
    booking_id: Annotated[int, Path(ge=1)],
    day: Annotated[dt.date, Query()],
    _principal: mutations.StudioWriter,
    time_zone: Annotated[str, Query(max_length=255)] = "",
) -> BookingSlots:
    try:
        display_zone = ZoneInfo(time_zone or config.TIMEZONE)
    except Exception as exc:
        raise mobile_auth.MobileAuthError(
            422, "booking.invalid_time_zone", "Unknown time zone."
        ) from exc
    con = db.connect()
    try:
        booking = _booking_detail(con, booking_id)
        if booking.status != "confirmed":
            raise mobile_auth.MobileAuthError(
                409, "booking.closed", "Only confirmed bookings can be rescheduled."
            )
        event = _event_for_booking(con, booking_id)
        business_zone = scheduling._biz_tz()
        day_start = dt.datetime.combine(day, dt.time(), business_zone).astimezone(dt.UTC)
        day_end = dt.datetime.combine(
            day + dt.timedelta(days=1), dt.time(), business_zone
        ).astimezone(dt.UTC)
        busy = gcal.free_busy(day_start, day_end)
        starts = scheduling._slots_utc(
            con,
            event,
            day,
            scheduling.now_utc(),
            busy,
            exclude_id=booking_id,
        )
    finally:
        con.close()
    mutations._private(response)
    return BookingSlots(
        day=day,
        time_zone=display_zone.key,
        items=[BookingSlot(start_at=value) for value in starts],
    )


@router.post(
    "/bookings/{booking_id}/cancel",
    response_model=Booking,
    tags=["owner scheduling commands"],
)
def cancel_booking(
    request: Request,
    response: Response,
    body: BookingCancelRequest,
    principal: mutations.StudioWriter,
    booking_id: Annotated[int, Path(ge=1)],
) -> Booking:
    job_id = None
    with mutations._immediate_transaction() as con:
        claim = mutations._claim_command(
            con,
            request,
            principal,
            f"booking.cancel:{booking_id}",
            mutations._request_payload(body, request, include_match=True),
        )
        if claim.replayed:
            value = mutations._replay(claim, Booking)
        else:
            before = _booking_detail(con, booking_id)
            mutations._require_current(request, _booking_etag(before))
            if before.status != "confirmed":
                raise mobile_auth.MobileAuthError(
                    409, "booking.closed", "This booking is no longer confirmed."
                )
            con.execute(
                """UPDATE bookings SET status='cancelled', cancel_reason=?,
                          cancelled_at=datetime('now') WHERE id=?""",
                (body.reason, booking_id),
            )
            value = _booking_detail(con, booking_id)
            audit.log(
                con,
                "booking",
                booking_id,
                "cancel",
                actor=_OWNER_ACTOR,
                diff={"reason": body.reason, "start_at": before.start_at},
            )
            mutations._finish_command(
                con,
                principal,
                claim,
                value,
                status_code=200,
                effect={"kind": "booking_cancel", "booking_id": booking_id},
            )
            job_id = _enqueue_effect(con, principal, claim.key)
    if job_id is not None:
        jobs.kick(job_id)
    mutations._private(response, etag=_booking_etag(value), replayed=claim.replayed)
    return value


@router.post(
    "/bookings/{booking_id}/reschedule",
    response_model=Booking,
    status_code=201,
    tags=["owner scheduling commands"],
)
def reschedule_booking(
    request: Request,
    response: Response,
    body: BookingRescheduleRequest,
    principal: mutations.StudioWriter,
    booking_id: Annotated[int, Path(ge=1)],
) -> Booking:
    start_at = body.start_at.astimezone(dt.UTC)
    day = start_at.astimezone(scheduling._biz_tz()).date()
    business_zone = scheduling._biz_tz()
    day_start = dt.datetime.combine(day, dt.time(), business_zone).astimezone(dt.UTC)
    day_end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), business_zone).astimezone(
        dt.UTC
    )
    busy = gcal.free_busy(day_start, day_end)
    job_id = None
    with mutations._immediate_transaction() as con:
        claim = mutations._claim_command(
            con,
            request,
            principal,
            f"booking.reschedule:{booking_id}",
            mutations._request_payload(body, request, include_match=True),
        )
        if claim.replayed:
            value = mutations._replay(claim, Booking)
        else:
            before = _booking_detail(con, booking_id)
            mutations._require_current(request, _booking_etag(before))
            if before.status != "confirmed":
                raise mobile_auth.MobileAuthError(
                    409, "booking.closed", "This booking is no longer confirmed."
                )
            event = _event_for_booking(con, booking_id)
            start_text = scheduling._fmt_utc(start_at)
            end_at = start_at + dt.timedelta(minutes=int(event["duration_min"]))
            allowed = {
                scheduling._fmt_utc(value)
                for value in scheduling._slots_utc(
                    con,
                    event,
                    day,
                    scheduling.now_utc(),
                    busy,
                    exclude_id=booking_id,
                )
            }
            if start_text not in allowed:
                raise mobile_auth.MobileAuthError(
                    409,
                    "booking.slot_unavailable",
                    "That time is no longer available. Choose another slot.",
                )
            source = con.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
            token = security.new_slug(20)
            new_id = con.execute(
                """INSERT INTO bookings
                   (token, event_type_id, name, email, phone, notes, start_utc,
                    end_utc, tz, reschedule_of, client_id, project_id,
                    venue_address, dish_count, parking_notes, style_refs,
                    onsite_contact)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    token,
                    source["event_type_id"],
                    source["name"],
                    source["email"],
                    source["phone"],
                    source["notes"],
                    start_text,
                    scheduling._fmt_utc(end_at),
                    body.time_zone or source["tz"],
                    booking_id,
                    source["client_id"],
                    source["project_id"],
                    source["venue_address"],
                    source["dish_count"],
                    source["parking_notes"],
                    source["style_refs"],
                    source["onsite_contact"],
                ),
            ).lastrowid
            con.execute(
                """UPDATE bookings SET status='cancelled', cancel_reason='Rescheduled',
                          cancelled_at=datetime('now') WHERE id=?""",
                (booking_id,),
            )
            value = _booking_detail(con, int(new_id))
            audit.log(
                con,
                "booking",
                booking_id,
                "reschedule",
                actor=_OWNER_ACTOR,
                diff={
                    "start_at": [before.start_at, value.start_at],
                    "replacement_id": value.id,
                },
            )
            mutations._finish_command(
                con,
                principal,
                claim,
                value,
                status_code=201,
                effect={"kind": "booking_reschedule", "booking_id": value.id},
            )
            job_id = _enqueue_effect(con, principal, claim.key)
    if job_id is not None:
        jobs.kick(job_id)
    mutations._private(response, etag=_booking_etag(value), replayed=claim.replayed)
    return value


def _decide_proposal(
    request: Request,
    response: Response,
    principal: mobile_auth.Principal,
    decision: Literal["accept", "decline"],
) -> delivery.DocumentDelivery:
    proposal_id = int(principal.resource_id)
    operation = f"proposal.{decision}:{proposal_id}"
    job_id = None
    notification_jobs: list[int] = []
    with mutations._immediate_transaction() as con:
        claim = mutations._claim_command(
            con,
            request,
            principal,
            operation,
            {"if_match": request.headers.get("If-Match", "").strip()},
        )
        if claim.replayed:
            value = mutations._replay(claim, delivery.DocumentDelivery)
        else:
            row = delivery._document_base("proposals", proposal_id, connection=con)
            before = _proposal_from_row(request, row, can_act=True)
            mutations._require_current(request, _document_etag(before))
            if row["status"] not in ("sent", "viewed"):
                raise mobile_auth.MobileAuthError(
                    409, "proposal.closed", "This proposal is no longer open for a response."
                )
            if decision == "accept":
                con.execute(
                    """UPDATE proposals SET status='accepted',
                              accepted_at=datetime('now') WHERE id=?""",
                    (proposal_id,),
                )
            else:
                con.execute(
                    "UPDATE proposals SET status='declined' WHERE id=?",
                    (proposal_id,),
                )
            updated = delivery._document_base("proposals", proposal_id, connection=con)
            value = _proposal_from_row(request, updated, can_act=True)
            audit.log(
                con,
                "proposal",
                proposal_id,
                decision,
                actor=_CLIENT_ACTOR,
                diff={
                    "status": [row["status"], value.status],
                    "session_id": principal.session_id,
                    "device": {
                        "name": principal.device_name,
                        "platform": principal.device_platform,
                        "app_version": principal.device_app_version,
                    },
                    "request_ip": security.client_ip(request),
                    "idempotency_key": claim.key,
                },
            )
            effect = None
            if decision == "accept":
                effect = {
                    "kind": "proposal_accept",
                    "proposal_id": proposal_id,
                    "project_id": int(row["project_id"]),
                    "label": f"Proposal accepted: {row['title']}",
                }
            mutations._finish_command(
                con,
                principal,
                claim,
                value,
                status_code=200,
                effect=effect,
            )
            verb = "accepted" if decision == "accept" else "declined"
            title, body = push_notifications.alert_copy("proposal_responses")
            notification_jobs = push_notifications.enqueue_owner_event_tx(
                con,
                dedupe_key=f"proposal.{verb}:{proposal_id}",
                category="proposal_responses",
                route=f"/app/projects/{row['project_id']}",
                title=title,
                body=body,
            )
            if effect is not None:
                job_id = _enqueue_effect(con, principal, claim.key)
    if job_id is not None:
        jobs.kick(job_id)
    try:
        push_notifications.kick(notification_jobs)
    except Exception:
        # The durable event remains queued for the notification sweeper.
        log.exception("proposal %s notification kick failed", proposal_id)
    mutations._private(response, etag=_document_etag(value), replayed=claim.replayed)
    return value


@router.post(
    "/client/proposal/accept",
    response_model=delivery.DocumentDelivery,
    tags=["client proposal commands"],
)
def accept_proposal(
    request: Request,
    response: Response,
    principal: ProposalResponder,
) -> delivery.DocumentDelivery:
    return _decide_proposal(request, response, principal, "accept")


@router.post(
    "/client/proposal/decline",
    response_model=delivery.DocumentDelivery,
    tags=["client proposal commands"],
)
def decline_proposal(
    request: Request,
    response: Response,
    principal: ProposalResponder,
) -> delivery.DocumentDelivery:
    return _decide_proposal(request, response, principal, "decline")
