"""Gmail SMTP — manual sends only, every send logged by the caller in emails_log.

Hosted identity (ADR 0055): every send goes out over the operator's one SMTP login,
but the *identity* is the serving context's. In a tenant context the display name is
the studio's and Reply-To defaults to the studio owner, so a client who hits reply
reaches their photographer — never the platform operator. Single-tenant mode is
byte-for-byte unchanged (SITE_NAME, no implicit Reply-To).
"""

import smtplib
from email.message import EmailMessage

from . import config


def configured() -> bool:
    return bool(config.GMAIL_USER and config.GMAIL_APP_PASSWORD)


def _tenant() -> dict | None:
    if config.SAAS_MODE:
        from . import saas

        return saas.current_tenant()
    return None


def sender_name() -> str:
    """Display name for outbound mail, ICS organizer lines, and email signatures:
    the tenant's studio name in hosted context, else the operator's SITE_NAME."""
    tenant = _tenant()
    return tenant["studio_name"] if tenant else config.SITE_NAME


def studio_inbox() -> str:
    """Where studio-bound notifications (new leads, booking copies) are delivered:
    the tenant owner's email in hosted context, else the operator's Gmail."""
    tenant = _tenant()
    return tenant["owner_email"] if tenant else config.GMAIL_USER


def _default_reply_to() -> str:
    tenant = _tenant()
    return tenant["owner_email"] if tenant else ""


def _hdr(value: str) -> str:
    """Strip CR/LF (and other control chars) from a value bound for an email header.

    Reply-To and To can carry attacker-supplied addresses (public contact/lead forms
    → reply_to=<their email>). A newline there is the classic SMTP header-injection
    vector (smuggling Bcc/extra headers). The modern EmailMessage policy already
    rejects most of this at send time; stripping first is defense in depth and turns
    a hard 500 into a clean send (ADR 0061)."""
    return "".join(ch for ch in (value or "") if ch not in "\r\n" and ch >= " ")[:998]


def _build_message(
    to: str, subject: str, body: str, reply_to: str = "", ics: dict | None = None
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = f"{sender_name()} <{config.GMAIL_USER}>"
    msg["To"] = _hdr(to)
    msg["Subject"] = _hdr(subject)
    reply_to = _hdr(reply_to or _default_reply_to())
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)
    if ics:
        msg.add_attachment(
            ics["content"].encode(),
            maintype="text",
            subtype="calendar",
            filename=ics["filename"],
            params={"method": ics.get("method", "REQUEST"), "charset": "UTF-8"},
        )
    return msg


def send(to: str, subject: str, body: str, reply_to: str = "", ics: dict | None = None) -> None:
    """Send a plain-text email, optionally with a calendar invite attached.

    `ics` = {"filename", "content", "method"} — content is the VCALENDAR text from
    ics.build(); method ("REQUEST"/"CANCEL") must match its METHOD so Gmail/Apple
    Mail render the in-line Accept/Decline (or removal) affordance."""
    msg = _build_message(to, subject, body, reply_to=reply_to, ics=ics)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as s:
        s.login(config.GMAIL_USER, config.GMAIL_APP_PASSWORD)
        s.send_message(msg)
