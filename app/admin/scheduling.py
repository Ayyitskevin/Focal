"""Scheduler admin — event types, weekly availability, date overrides, bookings.

The public/visitor side lives in app/public/scheduling.py; this is the owner's
console. Mutations go through db.tx() + audit.log so a change to what the public
booking page offers is observable (R14). Slug is a public URL token (/book/{slug})
so it is charset-validated and IMMUTABLE after create, like crop-preset slugs.
"""

import logging
import re

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import audit, booking_notify, db, gcal, scheduling, security
from ..render import templates

log = logging.getLogger("mise.admin.scheduling")
router = APIRouter(prefix="/admin/scheduling",
                   dependencies=[Depends(security.require_admin)])

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _min_to_hhmm(m: int | None) -> str:
    if m is None:
        return ""
    return f"{m // 60:02d}:{m % 60:02d}"


def _hhmm_to_min(s: str) -> int | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        h, m = s.split(":")
        v = int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="bad time")
    if not (0 <= v <= 1440):
        raise HTTPException(status_code=400, detail="time out of range")
    return v


def _posint(form, key: str, lo: int, hi: int, default: int = 0) -> int:
    raw = (form.get(key) or "").strip()
    if raw == "":
        return default
    try:
        v = int(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"bad {key}")
    if not (lo <= v <= hi):
        raise HTTPException(status_code=400, detail=f"{key} out of range ({lo}–{hi})")
    return v


def _get_event(event_id: int) -> "db.sqlite3.Row":
    e = db.one("SELECT * FROM event_types WHERE id=?", (event_id,))
    if not e:
        raise HTTPException(status_code=404)
    return e


def _global_week() -> list[dict]:
    """The default weekly schedule (event_type_id IS NULL), one row per weekday.
    The UI edits a single window per day; the engine supports more (date overrides
    cover exceptions)."""
    rows = db.all_("""SELECT weekday, MIN(start_min) AS s, MAX(end_min) AS e
                      FROM availability_rules WHERE event_type_id IS NULL
                      GROUP BY weekday""")
    by_wd = {r["weekday"]: r for r in rows}
    out = []
    for wd in range(7):
        r = by_wd.get(wd)
        out.append({"wd": wd, "label": WEEKDAYS[wd],
                    "on": r is not None,
                    "start": _min_to_hhmm(r["s"]) if r else "09:00",
                    "end": _min_to_hhmm(r["e"]) if r else "17:00"})
    return out


# ── main console ─────────────────────────────────────────────────────────────

_GERR = {
    "state": "Connection request expired or didn't match — please try again.",
    "denied": "Google sign-in was cancelled.",
    "exchange": "Google rejected the connection. Re-check the OAuth client config "
                "and try again.",
}


@router.get("", response_class=HTMLResponse)
async def home(request: Request):
    events = db.all_("""SELECT et.*,
                        (SELECT COUNT(*) FROM bookings b
                         WHERE b.event_type_id=et.id AND b.status='confirmed'
                           AND b.start_utc >= datetime('now')) AS upcoming
                        FROM event_types et ORDER BY et.position, et.id""")
    overrides = db.all_("""SELECT * FROM date_overrides WHERE event_type_id IS NULL
                           AND day >= date('now') ORDER BY day""")
    return templates.TemplateResponse(request, "admin/scheduling.html",
                                      {"events": events, "week": _global_week(),
                                       "overrides": overrides,
                                       "tz": scheduling.config.TIMEZONE,
                                       "gcal": gcal.status(),
                                       "g_error": _GERR.get(request.query_params.get("gerr"))})


@router.post("/availability")
async def save_availability(request: Request):
    """Replace the global weekly schedule from the 7-day form (idempotent)."""
    form = await request.form()
    rows = []
    for wd in range(7):
        if form.get(f"on_{wd}"):
            s = _hhmm_to_min(form.get(f"start_{wd}"))
            e = _hhmm_to_min(form.get(f"end_{wd}"))
            if s is None or e is None or e <= s:
                raise HTTPException(status_code=400,
                                    detail=f"{WEEKDAYS[wd]}: end must be after start")
            rows.append((wd, s, e))
    with db.tx() as con:
        con.execute("DELETE FROM availability_rules WHERE event_type_id IS NULL")
        for wd, s, e in rows:
            con.execute("INSERT INTO availability_rules (event_type_id,weekday,start_min,end_min) "
                        "VALUES (NULL,?,?,?)", (wd, s, e))
        audit.log(con, "availability", 0, "set_global",
                  diff={"days": [WEEKDAYS[wd] for wd, _, _ in rows]})
    return RedirectResponse("/admin/scheduling", status_code=303)


@router.post("/override")
async def add_override(request: Request, day: str = Form(...),
                       mode: str = Form("block"),
                       start: str = Form(""), end: str = Form("")):
    import datetime as dt
    try:
        dt.date.fromisoformat(day)
    except ValueError:
        raise HTTPException(status_code=400, detail="bad date")
    if mode == "hours":
        s, e = _hhmm_to_min(start), _hhmm_to_min(end)
        if s is None or e is None or e <= s:
            raise HTTPException(status_code=400, detail="end must be after start")
        avail, smin, emin = 1, s, e
    else:
        avail, smin, emin = 0, None, None
    with db.tx() as con:
        con.execute("""INSERT INTO date_overrides (event_type_id,day,available,start_min,end_min)
                       VALUES (NULL,?,?,?,?)""", (day, avail, smin, emin))
        audit.log(con, "date_override", 0, "add",
                  diff={"day": day, "blocked": avail == 0})
    return RedirectResponse("/admin/scheduling", status_code=303)


@router.post("/override/{override_id}/delete")
async def del_override(override_id: int):
    with db.tx() as con:
        con.execute("DELETE FROM date_overrides WHERE id=? AND event_type_id IS NULL",
                    (override_id,))
        audit.log(con, "date_override", override_id, "delete")
    return RedirectResponse("/admin/scheduling", status_code=303)


# ── Google Calendar connection (OAuth) ───────────────────────────────────────

_STATE_COOKIE = "g_oauth_state"


@router.get("/google/connect")
async def google_connect(request: Request):
    """Kick off the OAuth consent flow. A random state is stashed in an HttpOnly
    cookie and echoed to Google, then re-checked on callback (CSRF defence)."""
    if not gcal.configured():
        raise HTTPException(status_code=400, detail="Google client id/secret not set")
    state = security.new_slug(24)
    resp = RedirectResponse(gcal.auth_url(state), status_code=303)
    resp.set_cookie(_STATE_COOKIE, state, max_age=600, httponly=True,
                    samesite="lax", secure=scheduling.config.COOKIE_SECURE)
    return resp


@router.get("/google/callback")
async def google_callback(request: Request):
    """Consent return leg. Verify state, trade the code for a refresh token, and
    land back on the console with a success or error banner."""
    q = request.query_params
    cookie_state = request.cookies.get(_STATE_COOKIE)

    def _back(gerr: str | None = None):
        url = "/admin/scheduling" + (f"?gerr={gerr}" if gerr else "")
        r = RedirectResponse(url, status_code=303)
        r.delete_cookie(_STATE_COOKIE)
        return r

    if q.get("error"):
        return _back("denied")
    state = q.get("state") or ""
    if not cookie_state or not state or state != cookie_state:
        return _back("state")
    code = q.get("code") or ""
    if not code:
        return _back("exchange")
    try:
        gcal.exchange_code(code)
    except gcal.GcalError as e:
        log.warning("google oauth exchange failed: %s", e)
        return _back("exchange")
    with db.tx() as con:
        audit.log(con, "google_calendar", 1, "connect")
    return _back()


@router.post("/google/disconnect")
async def google_disconnect(request: Request):
    gcal.disconnect()
    with db.tx() as con:
        audit.log(con, "google_calendar", 1, "disconnect")
    return RedirectResponse("/admin/scheduling", status_code=303)


# ── event types ──────────────────────────────────────────────────────────────

@router.post("/event")
async def create_event(request: Request, name: str = Form(...), slug: str = Form(...),
                       duration_min: int = Form(30)):
    name, slug = name.strip(), slug.strip().lower()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    if not SLUG_RE.match(slug):
        raise HTTPException(status_code=400, detail="slug: lowercase letters, digits, hyphens")
    if db.one("SELECT 1 FROM event_types WHERE slug=?", (slug,)):
        raise HTTPException(status_code=400, detail="slug already in use")
    if not (5 <= duration_min <= 1440):
        raise HTTPException(status_code=400, detail="duration 5–1440 min")
    with db.tx() as con:
        cur = con.execute("INSERT INTO event_types (slug,name,duration_min) VALUES (?,?,?)",
                          (slug, name, duration_min))
        audit.log(con, "event_type", cur.lastrowid, "create",
                  diff={"slug": slug, "name": name})
        eid = cur.lastrowid
    return RedirectResponse(f"/admin/scheduling/event/{eid}", status_code=303)


@router.get("/event/{event_id}", response_class=HTMLResponse)
async def edit_event(request: Request, event_id: int):
    e = _get_event(event_id)
    return templates.TemplateResponse(request, "admin/scheduling_event.html",
                                      {"e": e, "base_url": scheduling.config.BASE_URL})


@router.post("/event/{event_id}")
async def update_event(request: Request, event_id: int):
    e = _get_event(event_id)
    form = await request.form()
    name = (form.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    duration = _posint(form, "duration_min", 5, 1440, e["duration_min"])
    fields = {
        "name": name,
        "description": (form.get("description") or "").strip(),
        "duration_min": duration,
        "location": (form.get("location") or "").strip(),
        "color": (form.get("color") or "#b3552e").strip()[:9],
        "buffer_before_min": _posint(form, "buffer_before_min", 0, 480),
        "buffer_after_min": _posint(form, "buffer_after_min", 0, 480),
        "min_notice_hours": _posint(form, "min_notice_hours", 0, 8760, 12),
        "max_per_day": _posint(form, "max_per_day", 0, 50),
        "booking_window_days": _posint(form, "booking_window_days", 1, 365, 60),
        "slot_step_min": _posint(form, "slot_step_min", 0, 480),
        "position": _posint(form, "position", 0, 999),
        "creates_notion_session": 1 if form.get("creates_notion_session") else 0,
    }
    sets = ", ".join(f"{k}=?" for k in fields)
    with db.tx() as con:
        con.execute(f"UPDATE event_types SET {sets} WHERE id=?",
                    (*fields.values(), event_id))
        audit.log(con, "event_type", event_id, "update",
                  diff={k: [e[k], v] for k, v in fields.items() if e[k] != v})
    return RedirectResponse(f"/admin/scheduling/event/{event_id}", status_code=303)


@router.post("/event/{event_id}/toggle")
async def toggle_event(event_id: int):
    e = _get_event(event_id)
    new = 0 if e["active"] else 1
    with db.tx() as con:
        con.execute("UPDATE event_types SET active=? WHERE id=?", (new, event_id))
        audit.log(con, "event_type", event_id, "activate" if new else "deactivate")
    return RedirectResponse("/admin/scheduling", status_code=303)


@router.post("/event/{event_id}/delete")
async def delete_event(event_id: int):
    e = _get_event(event_id)
    n = db.one("SELECT COUNT(*) AS n FROM bookings WHERE event_type_id=?", (event_id,))
    if n["n"]:
        # Bookings reference this event — deactivate instead of orphaning history.
        raise HTTPException(status_code=400,
                            detail="event has bookings; deactivate it instead of deleting")
    with db.tx() as con:
        con.execute("DELETE FROM event_types WHERE id=?", (event_id,))
        audit.log(con, "event_type", event_id, "delete", diff={"slug": e["slug"]})
    return RedirectResponse("/admin/scheduling", status_code=303)


# ── bookings list + admin cancel ─────────────────────────────────────────────

@router.get("/bookings", response_class=HTMLResponse)
async def bookings(request: Request):
    upcoming = db.all_("""SELECT b.*, e.name AS event_name FROM bookings b
                          JOIN event_types e ON e.id=b.event_type_id
                          WHERE b.status='confirmed' AND b.start_utc >= datetime('now')
                          ORDER BY b.start_utc""")
    past = db.all_("""SELECT b.*, e.name AS event_name FROM bookings b
                      JOIN event_types e ON e.id=b.event_type_id
                      WHERE b.status!='confirmed' OR b.start_utc < datetime('now')
                      ORDER BY b.start_utc DESC LIMIT 100""")
    return templates.TemplateResponse(request, "admin/scheduling_bookings.html",
                                      {"upcoming": upcoming, "past": past,
                                       "tz": scheduling.config.TIMEZONE})


@router.post("/booking/{booking_id}/cancel")
async def admin_cancel(booking_id: int):
    b = db.one("SELECT token FROM bookings WHERE id=?", (booking_id,))
    if not b:
        raise HTTPException(status_code=404)
    if scheduling.cancel(b["token"], "Cancelled by Kevin Lee Photography"):
        booking_notify.cancelled(booking_id, by_admin=True)
    return RedirectResponse("/admin/scheduling/bookings", status_code=303)
