"""Best-effort security alerts to Telegram (direct Bot API sendMessage).

Dormant unless MISE_TELEGRAM_TOKEN + MISE_TELEGRAM_CHAT_ID are set in .env. Sending
is a one-shot HTTP POST — it never calls getUpdates, so it can NEVER conflict with
the single Telegram polling consumer (MickeyBot) elsewhere on the fleet. Fire-and-
forget on a daemon thread: a slow/down Telegram must never block or stall an auth
path, so failures are logged and swallowed. Alerts fire only on ANOMALIES (lockouts
after repeated failures) — never on a normal login or a deploy restart — to avoid
alert fatigue.
"""

import logging
import threading
import urllib.parse
import urllib.request

from . import config

log = logging.getLogger("mise.alerts")


def is_enabled() -> bool:
    return bool(config.TELEGRAM_TOKEN and config.TELEGRAM_CHAT_ID)


def _send(text: str) -> None:
    url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage"
    data = urllib.parse.urlencode(
        {"chat_id": config.TELEGRAM_CHAT_ID, "text": text[:3800]}).encode()
    try:
        with urllib.request.urlopen(url, data=data, timeout=5) as r:
            r.read()
    except Exception as e:  # never let a notify failure surface into an auth path
        log.warning("security alert send failed: %s", e)


def security_alert(text: str) -> None:
    if not is_enabled():
        return
    threading.Thread(target=_send, args=(f"\U0001f510 Mise: {text}",),
                     daemon=True).start()
