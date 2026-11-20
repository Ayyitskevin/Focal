"""Inquiry data normalization and conversion shared by Studio and the native owner API."""

import datetime as dt
import sqlite3

from . import audit, notion_sync


class ConversionError(Exception):
    def __init__(self, status_code: int, code: str, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.code = code


def bounded_text(value: object, *, maximum: int, fallback: str | None = None) -> str | None:
    cleaned = str(value or "").strip()
    return cleaned[:maximum] or fallback


def parse_date(value: object) -> dt.date | None:
    raw = str(value or "").strip()
    if len(raw) != 10:
        return None
    try:
        return dt.date.fromisoformat(raw)
    except ValueError:
        return None


def link_booking_records(
    con: sqlite3.Connection, booking_id: int, inquiry_id: int, *, promote: bool
) -> tuple[int | None, int | None]:
    """Resolve and publish a booking's IDs inside the caller's immediate transaction.

    A converted mirror is authoritative, including links cleared by a deletion.
    Otherwise reuse the booking or its reschedule source before allocating records.
    A consultation only gains a project when an owner explicitly promotes it.
    """
    b = con.execute(
        """SELECT b.*, e.name AS event_name, e.creates_notion_session,
                  original.client_id AS original_client_id, original.project_id AS original_project_id,
                  i.converted_at, i.converted_client_id, i.converted_project_id
             FROM bookings b JOIN event_types e ON e.id=b.event_type_id
             LEFT JOIN bookings original ON original.id=b.reschedule_of
             JOIN inquiries i ON i.id=b.inquiry_id WHERE b.id=?""",
        (booking_id,),
    ).fetchone()
    if b is None or b["inquiry_id"] != inquiry_id:
        raise ValueError(f"Booking {booking_id} no longer has the expected mirror inquiry")
    if b["converted_at"]:
        client_id, project_id = b["converted_client_id"], b["converted_project_id"]
    else:
        client_id, project_id = b["client_id"], b["project_id"]
        if client_id is None and project_id is None:
            client_id, project_id = b["original_client_id"], b["original_project_id"]
        if client_id is None and project_id is not None:
            client_id = con.execute(
                "SELECT client_id FROM projects WHERE id=?", (project_id,)
            ).fetchone()["client_id"]
        if client_id is None:
            client_id = _find_or_create_client(
                con,
                name=b["name"],
                email=(b["email"] or "").strip(),
                phone=b["phone"] or "",
                notes=f"Auto-created from a Mise booking {b['created_at'][:10]}.",
            )
        if project_id is None and (b["creates_notion_session"] or promote):
            shoot_date = b["start_utc"][:10]
            project_id = con.execute(
                "INSERT INTO projects (client_id, title, shoot_date, notes) VALUES (?,?,?,?)",
                (
                    client_id,
                    f"{b['event_name']} — {shoot_date}",
                    shoot_date,
                    notion_sync.intake_summary(b) or None,
                ),
            ).lastrowid
        # An already linked booking with a cleared stamp can reflect an explicit Undo.
        # The owner writes the stamp and audit together below on manual promotion.
        if not promote and project_id is not None and b["project_id"] is None:
            stamp_conversion(con, inquiry_id, client_id, project_id)
    if (b["client_id"], b["project_id"]) != (client_id, project_id):
        con.execute(
            "UPDATE bookings SET client_id=?, project_id=? WHERE id=?",
            (client_id, project_id, booking_id),
        )
    return client_id, project_id


def stamp_conversion(
    con: sqlite3.Connection, inquiry_id: int, client_id: int | None, project_id: int | None
) -> None:
    con.execute(
        """UPDATE inquiries SET converted_at=datetime('now'),
              converted_client_id=?, converted_project_id=? WHERE id=?""",
        (client_id, project_id, inquiry_id),
    )


def convert_to_records(con: sqlite3.Connection, inquiry_id: int) -> tuple[int | None, int | None]:
    """Convert once and return the stored client and project IDs.

    Call first inside db.tx(). Take the write lock before checking the conversion
    stamp so a concurrent browser or iPhone retry observes the winning IDs.
    """
    con.execute("BEGIN IMMEDIATE")
    inquiry = con.execute("SELECT * FROM inquiries WHERE id=?", (inquiry_id,)).fetchone()
    if inquiry is None:
        raise ConversionError(404, "inquiry.not_found", "Inquiry not found.")
    if inquiry["converted_at"]:
        # ON DELETE SET NULL can leave a previously converted inquiry without links.
        return inquiry["converted_client_id"], inquiry["converted_project_id"]

    if inquiry["dismissed_at"]:
        raise ConversionError(
            409,
            "inquiry.dismissed",
            "Restore the inquiry before converting it.",
        )
    email = str(inquiry["email"] or "").strip()
    if len(email) > 2000:
        raise ConversionError(
            422,
            "inquiry.email_too_long",
            "The inquiry email is too long to convert safely.",
        )
    shoot_on = None
    if inquiry["kind"] == "booking" and str(inquiry["shoot_date"] or "").strip():
        shoot_on = parse_date(inquiry["shoot_date"])
        if shoot_on is None:
            raise ConversionError(
                422,
                "inquiry.shoot_date_invalid",
                "The booking shoot date must be a valid ISO date.",
            )
    booking = None
    if inquiry["kind"] == "booking":
        booking = con.execute(
            "SELECT id FROM bookings WHERE inquiry_id=?", (inquiry_id,)
        ).fetchone()
    if booking:
        client_id, project_id = link_booking_records(con, booking["id"], inquiry_id, promote=True)
    else:
        client_id, project_id = _create_inquiry_records(con, inquiry, email, shoot_on)
    stamp_conversion(con, inquiry_id, client_id, project_id)
    audit.log(
        con,
        "inquiry",
        inquiry_id,
        "convert",
        diff={
            "converted_client_id": [None, client_id],
            "converted_project_id": [None, project_id],
        },
        actor="owner",
    )
    return client_id, project_id


def _find_or_create_client(con, *, name, email, notes, company=None, phone=None):
    if email:
        existing = con.execute("SELECT id FROM clients WHERE email=?", (email,)).fetchone()
        if existing:
            return existing["id"]
    return con.execute(
        "INSERT INTO clients (name, company, email, phone, notes) VALUES (?,?,?,?,?)",
        (name, company, email or None, phone, notes),
    ).lastrowid


def _create_inquiry_records(con, inquiry, email, shoot_on):
    client_id = _find_or_create_client(
        con,
        name=bounded_text(inquiry["name"], maximum=2000, fallback="Unnamed client"),
        company=bounded_text(inquiry["business"], maximum=2000),
        email=email,
        notes=f"From inquiry {inquiry['created_at'][:10]}:\n{inquiry['message']}",
    )
    project_id = None
    if shoot_on is not None:
        service = bounded_text(inquiry["service"], maximum=1987, fallback="Shoot")
        title = f"{service} — {shoot_on.isoformat()}"
        project_id = int(
            con.execute(
                "INSERT INTO projects (client_id, title, shoot_date) VALUES (?,?,?)",
                (client_id, title, shoot_on.isoformat()),
            ).lastrowid
        )
    return client_id, project_id
