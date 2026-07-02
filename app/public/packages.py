"""Public package request pages.

Packages are a MicroSaaS-friendly lead capture layer: a visitor can pick a
starter offer, and the request lands in the existing inquiries inbox.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from .. import db, mailer, security
from ..render import templates

log = logging.getLogger("mise.public.packages")
router = APIRouter()


def get_public_package(slug: str) -> db.sqlite3.Row:
    package = db.one("SELECT * FROM packages WHERE slug=? AND active=1", (slug,))
    if not package:
        raise HTTPException(status_code=404)
    return package


def record_package_lead(
    package_id: int,
    *,
    name: str,
    email: str,
    event_date: str = "",
    message: str = "",
) -> int:
    package = db.get_or_404("SELECT * FROM packages WHERE id=?", (package_id,))
    details = [
        f"Package: {package['name']}",
        f"Package price: ${package['price_cents'] / 100:.2f}",
    ]
    if event_date.strip():
        details.append(f"Target date: {event_date.strip()}")
    if message.strip():
        details.append("")
        details.append(message.strip())
    inquiry_id = db.run(
        """INSERT INTO inquiries (name, email, message, service, shoot_date)
           VALUES (?,?,?,?,?)""",
        (
            name.strip(),
            email.strip(),
            "\n".join(details),
            package["name"],
            event_date.strip() or None,
        ),
    )
    return db.run(
        """INSERT INTO package_leads
           (package_id, name, email, event_date, message, inquiry_id)
           VALUES (?,?,?,?,?,?)""",
        (
            package_id,
            name.strip(),
            email.strip(),
            event_date.strip() or None,
            message.strip() or None,
            inquiry_id,
        ),
    )


@router.get("/packages/{slug}", response_class=HTMLResponse)
async def package_page(request: Request, slug: str):
    package = get_public_package(slug)
    return templates.TemplateResponse(
        request,
        "site/package.html",
        {"package": package, "sent": False, "error": None, "values": {}},
    )


@router.post("/packages/{slug}", response_class=HTMLResponse)
async def submit_package_request(
    request: Request,
    slug: str,
    name: str = Form(...),
    email: str = Form(...),
    event_date: str = Form(""),
    message: str = Form(""),
    website: str = Form(""),
):
    package = get_public_package(slug)
    values = {
        "name": name.strip(),
        "email": email.strip(),
        "event_date": event_date.strip(),
        "message": message.strip(),
    }
    if website.strip():
        return templates.TemplateResponse(
            request,
            "site/package.html",
            {"package": package, "sent": True, "error": None, "values": {}},
        )
    ip = security.client_ip(request)
    if security.inquiry_throttled(ip, security.INQUIRY_BUCKET_PACKAGE):
        return templates.TemplateResponse(
            request,
            "site/package.html",
            {
                "package": package,
                "sent": False,
                "error": "You've sent a few package requests recently. Give me a moment before sending another.",
                "values": values,
            },
            status_code=429,
        )
    if not values["name"] or not (
        "@" in values["email"] and "." in values["email"].rsplit("@", 1)[-1]
    ):
        return templates.TemplateResponse(
            request,
            "site/package.html",
            {
                "package": package,
                "sent": False,
                "error": "Please add your name and a valid email.",
                "values": values,
            },
            status_code=400,
        )
    security.inquiry_record(ip, security.INQUIRY_BUCKET_PACKAGE)
    lead_id = record_package_lead(
        package["id"],
        name=values["name"],
        email=values["email"],
        event_date=values["event_date"],
        message=values["message"],
    )
    if mailer.configured():
        try:
            mailer.send(
                mailer.studio_inbox(),
                f"New package request - {package['name']}",
                f"Name: {values['name']}\nEmail: {values['email']}\n"
                f"Package: {package['name']}\nDate: {values['event_date'] or '-'}\n\n"
                f"{values['message'] or ''}",
                reply_to=values["email"],
            )
        except Exception:
            log.exception("package lead %s stored but email failed", lead_id)
    log.info("package lead %s created for package %s", lead_id, package["slug"])
    return templates.TemplateResponse(
        request,
        "site/package.html",
        {"package": package, "sent": True, "error": None, "values": {}},
    )
