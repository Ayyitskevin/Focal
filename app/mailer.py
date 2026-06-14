"""Gmail SMTP — manual sends only, every send logged by the caller in emails_log."""

import smtplib
from email.message import EmailMessage

from . import config


def configured() -> bool:
    return bool(config.GMAIL_USER and config.GMAIL_APP_PASSWORD)


def send(to: str, subject: str, body: str, reply_to: str = "") -> None:
    msg = EmailMessage()
    msg["From"] = f"{config.SITE_NAME} <{config.GMAIL_USER}>"
    msg["To"] = to
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as s:
        s.login(config.GMAIL_USER, config.GMAIL_APP_PASSWORD)
        s.send_message(msg)
