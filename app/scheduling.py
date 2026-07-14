"""Calendly-style scheduling engine — pure-ish logic over the scheduler tables.

Responsibilities:
  * generate open time slots for an event type on a given local day, honouring
    weekly availability, date overrides, duration, slot step, buffers, minimum
    notice, per-day cap, and the booking window;
  * claim a slot atomically so two concurrent visitors cannot double-book.

Time model (see migration 033): availability is authored in business-local
minutes-from-midnight; booking instants are stored UTC. All wall-clock -> UTC
conversion happens here via zoneinfo, so DST is correct without stored offsets.

The claim path uses an explicit ``BEGIN IMMEDIATE`` transaction: the write lock
is taken BEFORE the open-slot re-check, which is what makes the check-then-insert
race-safe under SQLite/WAL (a second writer blocks on the lock, then re-checks and
sees the slot gone). Never trust a slot the client submits — ``book`` re-derives
the day's open slots inside the transaction and rejects anything not in that set.
"""

import datetime as dt
import logging
from collections import Counter
from zoneinfo import ZoneInfo

from . import booking_workflow, config, db, gcal, ics, security

log = logging.getLogger("mise.scheduling")

_UTC = dt.UTC


class SlotTaken(Exception):
    """Raised when a slot is no longer bookable (gone, blocked, or out of policy)."""


def _biz_tz() -> ZoneInfo:
    return ZoneInfo(config.TIMEZONE)


def _display_tz(name: str) -> ZoneInfo:
    """Visitor's tz for labels, falling back to business tz on anything invalid."""
    if name:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return _biz_tz()


def _parse_utc(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_UTC)


def _fmt_utc(d: dt.datetime) -> str:
    return d.astimezone(_UTC).strftime("%Y-%m-%d %H:%M:%S")


def now_utc() -> dt.datetime:
    return dt.datetime.now(_UTC)


# ── event-type lookups ───────────────────────────────────────────────────────


def active_event_types() -> list:
    return db.all_("SELECT * FROM event_types WHERE active=1 ORDER BY position, id")


def event_by_slug(slug: str):
    return db.one("SELECT * FROM event_types WHERE slug=? AND active=1", (slug,))


# ── availability windows for a local day ─────────────────────────────────────


def _windows_for_day(con, et, day: dt.date) -> list[tuple[int, int]]:
    """Return [(start_min, end_min), ...] of business-local availability for `day`.

    A date override (event-specific preferred over global) wins outright: a block
    yields []; a custom-hours override yields its single window. Otherwise the
    weekly rules for that weekday apply (event-specific preferred over global)."""
    iso = day.isoformat()
    ov = con.execute(
        """SELECT available, start_min, end_min FROM date_overrides
           WHERE day=? AND (event_type_id=? OR event_type_id IS NULL)
           ORDER BY event_type_id IS NULL LIMIT 1""",
        (iso, et["id"]),
    ).fetchone()
    if ov is not None:
        if not ov["available"] or ov["start_min"] is None or ov["end_min"] is None:
            return []
        return [(ov["start_min"], ov["end_min"])]

    wd = day.weekday()
    rows = con.execute(
        """SELECT start_min, end_min FROM availability_rules
           WHERE event_type_id=? AND weekday=? ORDER BY start_min""",
        (et["id"], wd),
    ).fetchall()
    if not rows:
        rows = con.execute(
            """SELECT start_min, end_min FROM availability_rules
               WHERE event_type_id IS NULL AND weekday=? ORDER BY start_min""",
            (wd,),
        ).fetchall()
    return [(r["start_min"], r["end_min"]) for r in rows]


def _overlaps(
    con, et, start_utc: dt.datetime, end_utc: dt.datetime, exclude_id: int | None
) -> bool:
    """True if the new booking collides with any confirmed booking. Both sides are
    padded by their OWN buffers (the new one in Python, existing ones in SQL), so a
    buffer protects travel/turnaround time symmetrically — a new slot can sit no
    closer than buffer minutes to an existing booking, regardless of event type."""
    lo = _fmt_utc(start_utc - dt.timedelta(minutes=et["buffer_before_min"]))
    hi = _fmt_utc(end_utc + dt.timedelta(minutes=et["buffer_after_min"]))
    row = con.execute(
        """SELECT COUNT(*) AS n FROM bookings b
           JOIN event_types e ON e.id=b.event_type_id
           WHERE b.status='confirmed'
             AND datetime(b.start_utc, '-'||e.buffer_before_min||' minutes') < ?
             AND datetime(b.end_utc,   '+'||e.buffer_after_min ||' minutes') > ?
             AND (? IS NULL OR b.id != ?)""",
        (hi, lo, exclude_id, exclude_id),
    ).fetchone()
    return row["n"] > 0


def _busy_conflict(
    start_utc: dt.datetime, end_utc: dt.datetime, busy: list[tuple[dt.datetime, dt.datetime]] | None
) -> bool:
    """True if [start_utc, end_utc) overlaps any busy interval already on Kevin's
    Google calendar. `busy` is None when the calendar isn't available (fail-open:
    hide nothing). Calendar buffers are intentionally NOT applied here — an
    external event blocks only its own wall-clock span, not the Mise turnaround."""
    if not busy:
        return False
    return any(bs < end_utc and be > start_utc for bs, be in busy)


def _local_slot_instants(day: dt.date, minute: int, tz: ZoneInfo) -> list[dt.datetime]:
    """Return the real UTC instants represented by one local wall-clock time.

    A spring-forward gap has no result. An ordinary time has one result. A
    fall-back fold has two results, in chronological order. Round-tripping each
    fold through UTC is the reliable ZoneInfo test; attaching a timezone directly
    otherwise invents nonexistent spring times and collapses fall ambiguity.
    """
    wall_time = dt.datetime.combine(day, dt.time()) + dt.timedelta(minutes=minute)
    instants: list[dt.datetime] = []
    for fold in (0, 1):
        local = wall_time.replace(tzinfo=tz, fold=fold)
        instant = local.astimezone(_UTC)
        round_trip = instant.astimezone(tz)
        if round_trip.replace(tzinfo=None) != wall_time or round_trip.fold != fold:
            continue
        if instant not in instants:
            instants.append(instant)
    return instants


def _day_count(con, et, day: dt.date, exclude_id: int | None = None) -> int:
    """Confirmed bookings for this event on this LOCAL day (cap accounting).

    A reschedule evaluates the destination as if its source booking has already
    been released. The exclusion therefore applies to both overlap and daily-cap
    checks; every other availability rule still applies unchanged.
    """
    tz = _biz_tz()
    start = dt.datetime.combine(day, dt.time(), tz).astimezone(_UTC)
    end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), tz).astimezone(_UTC)
    row = con.execute(
        """SELECT COUNT(*) AS n FROM bookings
           WHERE status='confirmed' AND event_type_id=?
             AND start_utc >= ? AND start_utc < ?
             AND (? IS NULL OR id != ?)""",
        (et["id"], _fmt_utc(start), _fmt_utc(end), exclude_id, exclude_id),
    ).fetchone()
    return row["n"]


def _slots_utc(
    con,
    et,
    day: dt.date,
    ref_utc: dt.datetime,
    busy: list[tuple[dt.datetime, dt.datetime]] | None = None,
    *,
    exclude_id: int | None = None,
) -> list[dt.datetime]:
    """Open slot start instants (UTC) for `day`, after all policy filters.

    `busy` (optional) is Google free/busy intervals for a range covering `day`;
    a slot that overlaps one is dropped so Mise never offers a time Kevin is
    already booked elsewhere. None = calendar unavailable -> no extra filtering."""
    today_local = ref_utc.astimezone(_biz_tz()).date()
    if day < today_local or (day - today_local).days > et["booking_window_days"]:
        return []
    if et["max_per_day"] and _day_count(con, et, day, exclude_id) >= et["max_per_day"]:
        return []

    tz = _biz_tz()
    dur = dt.timedelta(minutes=et["duration_min"])
    step = et["slot_step_min"] or et["duration_min"]
    notice_cutoff = ref_utc + dt.timedelta(hours=et["min_notice_hours"])
    window_cutoff = ref_utc + dt.timedelta(days=et["booking_window_days"])
    out: list[dt.datetime] = []
    for win_start, win_end in _windows_for_day(con, et, day):
        m = win_start
        while m + et["duration_min"] <= win_end:
            for start_utc in _local_slot_instants(day, m, tz):
                end_utc = start_utc + dur
                if (
                    start_utc >= notice_cutoff
                    and start_utc <= window_cutoff
                    and not _busy_conflict(start_utc, end_utc, busy)
                    and not _overlaps(con, et, start_utc, end_utc, exclude_id)
                ):
                    out.append(start_utc)
            m += step
    return out


# ── public API ───────────────────────────────────────────────────────────────


def slot_starts_for_day(
    et,
    day: dt.date,
    *,
    exclude_id: int | None = None,
) -> list[dt.datetime]:
    """Return server-computed UTC slot starts for one business-local day.

    ``exclude_id`` is used by reschedule previews so the confirmed source is
    released from both overlap and daily-cap accounting. This is still an
    advisory read: ``book_in_transaction`` re-derives the open set while
    holding the writer lock before it commits a booking transition.
    """
    tz = _biz_tz()
    ref = now_utc()
    today_local = ref.astimezone(tz).date()
    if day < today_local or (day - today_local).days > et["booking_window_days"]:
        return []
    day_start = dt.datetime.combine(day, dt.time(), tz).astimezone(_UTC)
    day_end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), tz).astimezone(_UTC)
    busy = gcal.free_busy(day_start, day_end)
    con = db.connect()
    try:
        return _slots_utc(
            con,
            et,
            day,
            ref,
            busy,
            exclude_id=exclude_id,
        )
    finally:
        con.close()


def slots_for_day(et, day: dt.date, visitor_tz: str = "") -> list[dict]:
    """Render-ready open slots for `day`: each item has the UTC value (form
    payload) plus a label in the visitor's timezone (falling back to business)."""
    disp = _display_tz(visitor_tz)
    starts = slot_starts_for_day(et, day)
    rendered = []
    for s in starts:
        local = s.astimezone(disp)
        rendered.append((s, local, local.strftime("%-I:%M %p").lstrip("0")))
    label_counts = Counter(label for _, _, label in rendered)
    duplicate_labels = {label for label, count in label_counts.items() if count > 1}
    out = []
    for instant, local, label in rendered:
        if label in duplicate_labels:
            offset = local.strftime("%z")
            label = f"{label} (UTC{offset[:3]}:{offset[3:]})"
        out.append({"utc": _fmt_utc(instant), "label": label})
    return out


def days_with_slots(et, start_day: dt.date, n_days: int) -> set[str]:
    """ISO days in [start_day, start_day+n_days) that have at least one open slot —
    used to light up the month picker without an HTMX round-trip per day."""
    tz = _biz_tz()
    win_start = dt.datetime.combine(start_day, dt.time(), tz).astimezone(_UTC)
    win_end = dt.datetime.combine(start_day + dt.timedelta(days=n_days), dt.time(), tz).astimezone(
        _UTC
    )
    busy = gcal.free_busy(win_start, win_end)
    con = db.connect()
    try:
        ref = now_utc()
        return {
            d.isoformat()
            for i in range(n_days)
            for d in [start_day + dt.timedelta(days=i)]
            if _slots_utc(con, et, d, ref, busy)
        }
    finally:
        con.close()


def book_in_transaction(
    con,
    et,
    start_utc_str: str,
    name: str,
    email: str,
    phone: str,
    notes: str,
    visitor_tz: str,
    exclude_id: int | None = None,
) -> tuple[int, str]:
    """Claim a slot inside the caller's open immediate transaction.

    Raises SlotTaken if the submitted instant is not currently an open slot
    (gone to a race, blocked, out of notice/window, or never valid). A reschedule
    may exclude its source booking from overlap and daily-cap accounting, but it
    never bypasses notice, booking-window, availability, or date-override policy.

    The caller owns commit/rollback. Keeping this primitive transaction-aware lets
    a reschedule commit its replacement, source cancellation, audit row, and API
    replay receipt as one indivisible unit while the ordinary book path uses the
    exact same slot validator.
    """
    try:
        start_utc = _parse_utc(start_utc_str)
    except ValueError:
        raise SlotTaken("malformed time")
    end_utc = start_utc + dt.timedelta(minutes=et["duration_min"])
    day_local = start_utc.astimezone(_biz_tz()).date()
    ref = now_utc()
    open_starts = {
        _fmt_utc(slot) for slot in _slots_utc(con, et, day_local, ref, exclude_id=exclude_id)
    }
    if start_utc_str not in open_starts:
        raise SlotTaken("slot no longer available")
    if exclude_id is not None:
        # A chained reschedule must retire the prior replacement workflow under
        # the same writer lock that claims its next slot. If provider work is
        # already running, fail before inserting another confirmed booking.
        booking_workflow.supersede_replacement(con, exclude_id)
    token = security.new_slug(20)
    calendar_uid = ics.new_uid(token)
    cur = con.execute(
        """INSERT INTO bookings (token, event_type_id, name, email, phone,
                                 notes, start_utc, end_utc, tz, reschedule_of,
                                 calendar_uid)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            token,
            et["id"],
            name,
            email,
            phone,
            notes,
            start_utc_str,
            _fmt_utc(end_utc),
            visitor_tz,
            exclude_id,
            calendar_uid,
        ),
    )
    return cur.lastrowid, token


def book(
    et,
    start_utc_str: str,
    name: str,
    email: str,
    phone: str,
    notes: str,
    visitor_tz: str,
    exclude_id: int | None = None,
) -> tuple[int, str]:
    """Atomically claim a slot. Returns (booking_id, manage_token).

    The open set is re-derived after the write lock is acquired, so a competing
    writer blocks and then observes the first writer's booking.
    """
    con = db.connect()
    con.isolation_level = None
    try:
        con.execute("BEGIN IMMEDIATE")
        result = book_in_transaction(
            con,
            et,
            start_utc_str,
            name,
            email,
            phone,
            notes,
            visitor_tz,
            exclude_id,
        )
        con.execute("COMMIT")
        return result
    except Exception:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        con.close()


def reschedule(
    et,
    start_utc_str: str,
    name: str,
    email: str,
    phone: str,
    notes: str,
    visitor_tz: str,
    source_booking_id: int,
) -> tuple[int, str]:
    """Atomically claim a replacement slot and cancel its confirmed source."""
    con = db.connect()
    con.isolation_level = None
    try:
        con.execute("BEGIN IMMEDIATE")
        result = book_in_transaction(
            con,
            et,
            start_utc_str,
            name,
            email,
            phone,
            notes,
            visitor_tz,
            exclude_id=source_booking_id,
        )
        if not cancel_in_transaction(con, source_booking_id, "Rescheduled"):
            raise SlotTaken("source booking is no longer confirmed")
        con.execute("COMMIT")
        return result
    except Exception:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        con.close()


def booking_by_token(token: str):
    return db.one(
        """SELECT b.*, e.name AS event_name, e.slug AS event_slug,
                  e.duration_min, e.location, e.min_notice_hours
           FROM bookings b JOIN event_types e ON e.id=b.event_type_id
           WHERE b.token=?""",
        (token,),
    )


def cancel_in_transaction(con, booking_id: int, reason: str = "") -> bool:
    """Cancel by id inside the caller's immediate transaction.

    Superseding pending delivery and the confirmed→cancelled transition share
    one writer lock. Running replacement effects raise ``WorkflowBusy`` so no
    lifecycle entry point can race provider I/O and resurrect stale state.
    """
    row = con.execute(
        "SELECT status FROM bookings WHERE id=?",
        (booking_id,),
    ).fetchone()
    if row is None or row["status"] != "confirmed":
        return False
    booking_workflow.supersede_replacement(con, booking_id)
    cur = con.execute(
        """UPDATE bookings SET status='cancelled', cancel_reason=?,
                  cancelled_at=datetime('now')
           WHERE id=? AND status='confirmed'""",
        (reason, booking_id),
    )
    return cur.rowcount > 0


def cancel(token: str, reason: str = "") -> bool:
    """Atomically cancel a confirmed booking by manage token.

    Returns True only if a row actually flipped, so a double-click or stale link
    cannot fire two cancellations. ``WorkflowBusy`` is deliberately propagated.
    """
    con = db.connect()
    con.isolation_level = None
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT id FROM bookings WHERE token=?", (token,)).fetchone()
        changed = bool(row and cancel_in_transaction(con, int(row["id"]), reason))
        con.execute("COMMIT")
        return changed
    except Exception:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        con.close()
