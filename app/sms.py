"""Two-way SMS via Quo (formerly OpenPhone) — provider-agnostic adapter.

The Inbox treats SMS exactly like email: a thread of messages hanging off an
inquiry. This module is the ONLY place that knows Quo's wire format, so swapping
providers later (Twilio, etc.) means rewriting this file and nothing else.

Ships INERT: with no Quo keys in .env, configured() is false — outbound send is a
no-op-by-refusal (raises SmsError, the route greys the SMS toggle) and the inbound
/webhooks/quo route returns 503. Email keeps flowing through mailer.py unchanged.

Verified against live Quo docs (quo.com/docs, 2026-06): the send endpoint + auth
header still follow OpenPhone's v1 API (POST api.openphone.com/v1/messages,
`Authorization: <key>` with NO Bearer prefix), but the inbound webhook signing was
modernized in the rebrand to the Standard-Webhooks (Svix-compatible) scheme — three
headers + a whsec_ secret (see verify_webhook). verify_webhook fails CLOSED, so a
scheme mismatch rejects inbound (safe) rather than trusting it.
"""

import base64
import hashlib
import hmac
import json
import logging
import time
import urllib.error
import urllib.request

from . import config

log = logging.getLogger("mise.sms")


class SmsError(Exception):
    """Any reason a text could not be sent. Message is safe to surface in admin
    (no secrets, no stack)."""


def configured() -> bool:
    """Armed only when an API key AND a from-number are set. Either unset -> the
    Inbox's SMS channel stays cleanly dormant."""
    return bool(config.QUO_API_KEY and config.QUO_NUMBER)


def send(to: str, body: str) -> str:
    """Send one SMS from the business Quo number to `to` (E.164). Returns the
    provider message id (stored on the messages row for idempotency/audit).

    Raises SmsError on every failure path so the caller writes nothing on failure."""
    if not configured():
        raise SmsError("SMS is not configured")
    to = (to or "").strip()
    body = (body or "").strip()
    if not to:
        raise SmsError("no recipient phone number")
    if not body:
        raise SmsError("message body is empty")
    req = urllib.request.Request(
        f"{config.QUO_API_BASE}/messages", method="POST",
        data=json.dumps({"from": config.QUO_NUMBER, "to": [to],
                         "content": body}).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": config.QUO_API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=config.QUO_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise SmsError(f"Quo returned HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError) as e:
        raise SmsError(f"Quo unreachable: {e.reason if hasattr(e, 'reason') else e}")
    except (ValueError, json.JSONDecodeError):
        raise SmsError("Quo returned an unreadable response")
    # OpenPhone/Quo nests the created message under "data": {"id": ...}; tolerate a
    # flat {"id": ...} too. A missing id is non-fatal — the text went out — so fall
    # back to "" (the messages row simply carries no provider id).
    msg = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    msg_id = (msg.get("id") or "").strip() if isinstance(msg, dict) else ""
    log.info("sms sent via Quo to %s (%d chars, id=%s)", to, len(body), msg_id or "?")
    return msg_id


def verify_webhook(raw: bytes, wh_id: str, wh_timestamp: str, wh_signature: str) -> bool:
    """Verify an inbound Quo webhook signature. Fails CLOSED (returns False) on any
    missing header/secret, stale timestamp, or malformed value — never trust an
    unverifiable payload.

    Scheme (Quo / Standard Webhooks / Svix, verified vs quo.com docs 2026-06):
    three headers `webhook-id`, `webhook-timestamp`, `webhook-signature`. The signing
    secret is `whsec_<base64>`; the HMAC key is base64-decode(secret minus the whsec_
    prefix). Signed content is "<id>.<timestamp>.<rawbody>"; signature =
    base64(HMAC-SHA256(key, content)). `webhook-signature` is a space-separated list
    of "v1,<base64sig>" entries — a timing-safe match on ANY entry passes. The
    timestamp must be within 5 minutes (replay guard)."""
    secret = config.QUO_WEBHOOK_SECRET
    if not (secret and wh_id and wh_timestamp and wh_signature):
        return False
    try:
        if abs(time.time() - int(wh_timestamp)) > 300:
            return False
    except (ValueError, TypeError):
        return False
    if secret.startswith("whsec_"):
        secret = secret[len("whsec_"):]
    try:
        key = base64.b64decode(secret)
    except (ValueError, TypeError):
        return False
    signed = f"{wh_id}.{wh_timestamp}.".encode() + raw
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    for entry in wh_signature.split():
        version, _, provided = entry.partition(",")
        if version == "v1" and hmac.compare_digest(expected, provided):
            return True
    return False
