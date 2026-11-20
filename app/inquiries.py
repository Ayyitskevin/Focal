"""Inquiry data normalization and conversion shared by Studio and the native owner API."""

import datetime as dt
import sqlite3

from . import audit


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
    name = bounded_text(inquiry["name"], maximum=2000, fallback="Unnamed client")
    business = bounded_text(inquiry["business"], maximum=2000)
    shoot_on = None
    if inquiry["kind"] == "booking" and str(inquiry["shoot_date"] or "").strip():
        shoot_on = parse_date(inquiry["shoot_date"])
        if shoot_on is None:
            raise ConversionError(
                422,
                "inquiry.shoot_date_invalid",
                "The booking shoot date must be a valid ISO date.",
            )
    existing = (
        con.execute("SELECT id FROM clients WHERE email=?", (email,)).fetchone() if email else None
    )
    if existing:
        client_id = int(existing["id"])
    else:
        client_id = int(
            con.execute(
                "INSERT INTO clients (name, company, email, notes) VALUES (?,?,?,?)",
                (
                    name,
                    business,
                    email or None,
                    f"From inquiry {inquiry['created_at'][:10]}:\n{inquiry['message']}",
                ),
            ).lastrowid
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
    con.execute(
        """UPDATE inquiries SET converted_at=datetime('now'),
              converted_client_id=?, converted_project_id=? WHERE id=?""",
        (client_id, project_id, inquiry_id),
    )
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
