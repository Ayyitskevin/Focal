"""Inbox — inbound inquiries as a conversation-style triage view.

Honest adaptation of the Admin Inbox prototype: that mock is a two-way SMS/email
messenger, but Mise has no SMS channel and email send is manual-only (Gmail SMTP,
human-in-the-loop). So this reads the REAL data Mise has — public-form inquiries —
in the prototype's 3-pane layout: thread list · the inbound message (read-only,
reply by email) · contact details + the real convert actions (quote / client /
dismiss) that already live in studio.py. No fake composer, no invented channel.
"""

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import config, db, mailer, security
from ..render import templates

log = logging.getLogger("mise.admin.inbox")
router = APIRouter(prefix="/admin/inbox",
                   dependencies=[Depends(security.require_admin)])

_TABS = ["all", "bookings", "archived"]

# Deterministic avatar tints by inquiry id — same forest/clay/teal family the
# prototype hand-picked, cycled so each thread reads as a distinct contact.
_AVATARS = [
    ("#7C2F38", "#F3F0E2"), ("#2f6d8a", "#FFFFFF"),
    ("#2f7d57", "#FFFFFF"), ("#9a7a2c", "#FFFFFF"), ("#143C2F", "#F3F0E2"),
]


def _initials(name: str) -> str:
    parts = [p for p in (name or "").split() if p]
    if not parts:
        return "#"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _channel(inq) -> dict:
    """Booking-form inquiries vs general messages — the only two real kinds."""
    if inq["kind"] == "booking":
        return {"ch_label": "Booking", "ch_color": "#9a7a2c", "ch_bg": "#f7ecd2"}
    return {"ch_label": "Inquiry", "ch_color": "#2f6d8a", "ch_bg": "#ddeef0"}


def _stage(inq) -> dict:
    if inq["converted_at"]:
        return {"stage": "Converted", "stage_color": "#2f7d57", "stage_bg": "#e1f2e9"}
    if inq["dismissed_at"]:
        return {"stage": "Dismissed", "stage_color": "#5C6A5E", "stage_bg": "#ecefe6"}
    if inq["kind"] == "booking":
        return {"stage": "Booking", "stage_color": "#9a7a2c", "stage_bg": "#f7ecd2"}
    return {"stage": "Lead", "stage_color": "#7C2F38", "stage_bg": "#f3e3e5"}


def _thread_row(inq, active_id):
    av = _AVATARS[inq["id"] % len(_AVATARS)]
    msg = (inq["message"] or "").strip().replace("\n", " ")
    return {
        "id": inq["id"], "name": inq["business"] or inq["name"] or "Unknown",
        "initials": _initials(inq["business"] or inq["name"]),
        "av_bg": av[0], "av_color": av[1],
        "time": inq["created_at"], "preview": msg or "(no message)",
        "active": inq["id"] == active_id,
        "unread": not inq["emailed"] and not inq["converted_at"] and not inq["dismissed_at"],
        **_channel(inq),
    }


def _reply_subject(inq) -> str:
    kind = "booking request" if inq["kind"] == "booking" else "inquiry"
    return f"Re: your {kind} — {config.SITE_NAME}"


def _detail_rows(inq) -> list[dict]:
    rows = []
    if inq["email"]:
        rows.append({"k": "Email", "v": inq["email"]})
    if inq["business"]:
        rows.append({"k": "Business", "v": inq["business"]})
    rows.append({"k": "Source", "v": "Booking form" if inq["kind"] == "booking"
                 else "Inquiry form"})
    if inq["service"]:
        rows.append({"k": "Interested in", "v": inq["service"]})
    if inq["shoot_date"]:
        rows.append({"k": "Shoot date", "v": inq["shoot_date"]})
    return rows


def _active_ctx(inq) -> dict:
    av = _AVATARS[inq["id"] % len(_AVATARS)]
    return {
        "id": inq["id"], "name": inq["business"] or inq["name"] or "Unknown",
        "contact_name": inq["name"], "initials": _initials(inq["business"] or inq["name"]),
        "av_bg": av[0], "av_color": av[1], "email": inq["email"],
        "message": inq["message"], "created_at": inq["created_at"],
        "converted_project_id": inq["converted_project_id"],
        "converted_client_id": inq["converted_client_id"],
        "is_converted": bool(inq["converted_at"]),
        "is_dismissed": bool(inq["dismissed_at"]),
        "is_replied": bool(inq["emailed"]),
        "reply_subject": _reply_subject(inq),
        "sub": (inq["email"] or "") + (" · booking request" if inq["kind"] == "booking" else ""),
        "details": _detail_rows(inq),
        **_channel(inq), **_stage(inq),
    }


@router.get("", response_class=HTMLResponse)
async def inbox(request: Request, tab: str = "all", sel: int | None = None):
    if tab not in _TABS:
        tab = "all"
    if tab == "archived":
        where = "converted_at IS NOT NULL OR dismissed_at IS NOT NULL"
        order = "ORDER BY COALESCE(dismissed_at, converted_at) DESC"
    elif tab == "bookings":
        where = "converted_at IS NULL AND dismissed_at IS NULL AND kind='booking'"
        order = "ORDER BY created_at DESC"
    else:
        where = "converted_at IS NULL AND dismissed_at IS NULL"
        order = "ORDER BY created_at DESC"
    rows = db.all_(f"SELECT * FROM inquiries WHERE {where} {order} LIMIT 100")

    counts = {
        "all": db.one("SELECT COUNT(*) n FROM inquiries "
                      "WHERE converted_at IS NULL AND dismissed_at IS NULL")["n"],
        "bookings": db.one("SELECT COUNT(*) n FROM inquiries WHERE converted_at IS NULL "
                           "AND dismissed_at IS NULL AND kind='booking'")["n"],
        "archived": db.one("SELECT COUNT(*) n FROM inquiries "
                           "WHERE converted_at IS NOT NULL OR dismissed_at IS NOT NULL")["n"],
    }

    active = None
    if rows:
        chosen = next((r for r in rows if r["id"] == sel), rows[0])
        active = _active_ctx(chosen)

    return templates.TemplateResponse(request, "admin/inbox.html", {
        "tab": tab, "counts": counts,
        "threads": [_thread_row(r, active["id"] if active else None) for r in rows],
        "active": active,
        "mail_configured": mailer.configured(),
    })


@router.post("/{inquiry_id}/reply")
async def reply(inquiry_id: int, tab: str = Form("all"),
                subject: str = Form(...), message: str = Form(...)):
    """Reply to an inquiry from inside Mise — manual Gmail SMTP send, logged.

    Mirrors app.admin.emails: Kevin clicks Send, nothing auto-sends. The send is
    recorded in emails_log (doc_kind='other') and the inquiry is marked emailed so
    the unread dot clears and the thread shows as replied."""
    inq = db.one("SELECT * FROM inquiries WHERE id=?", (inquiry_id,))
    if not inq:
        raise HTTPException(status_code=404)
    if not inq["email"]:
        raise HTTPException(status_code=400, detail="no email on file for this inquiry")
    if not mailer.configured():
        raise HTTPException(status_code=503, detail="email is not configured")
    subject, message = subject.strip(), message.strip()
    if not subject or not message:
        raise HTTPException(status_code=400, detail="subject and message required")
    try:
        mailer.send(inq["email"], subject, message)
    except Exception:
        log.exception("inbox reply send failed for inquiry %s", inquiry_id)
        raise HTTPException(status_code=502, detail="SMTP send failed — check logs")
    db.run("""INSERT INTO emails_log (project_id, doc_kind, doc_id, to_email, subject)
              VALUES (?, 'other', ?, ?, ?)""",
           (inq["converted_project_id"], inquiry_id, inq["email"], subject))
    db.run("UPDATE inquiries SET emailed=1 WHERE id=?", (inquiry_id,))
    log.info("inbox reply sent for inquiry %s", inquiry_id)
    if tab not in _TABS:
        tab = "all"
    return RedirectResponse(f"/admin/inbox?tab={tab}&sel={inquiry_id}", status_code=303)
