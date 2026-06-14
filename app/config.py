"""Mise configuration — env-driven, .env loaded if present (systemd uses EnvironmentFile)."""

import os
from pathlib import Path

_ENV_FILE = os.environ.get("MISE_ENV_FILE", "/opt/mise/.env")


def _load_env_file(path: str) -> None:
    p = Path(path)
    if not p.is_file():
        return
    try:
        text = p.read_text()
    except PermissionError:
        # Under systemd the .env is root/owner-readable only and already
        # injected via EnvironmentFile — nothing to do here.
        return
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env_file(_ENV_FILE)

def _b(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).lower() in ("1", "true", "yes")

HOST = os.environ.get("MISE_HOST", "127.0.0.1")
PORT = int(os.environ.get("MISE_PORT", "8400"))
BASE_URL = os.environ.get("MISE_BASE_URL", f"http://localhost:{PORT}")

DATA_DIR = Path(os.environ.get("MISE_DATA_DIR", "/opt/mise/data"))
DB_PATH = DATA_DIR / "mise.db"
MEDIA_DIR = DATA_DIR / "media"
ZIP_DIR = DATA_DIR / "zips"
TMP_DIR = DATA_DIR / "tmp"
BRAND_DIR = DATA_DIR / "brand"

SECRET_KEY = os.environ.get("MISE_SECRET_KEY", "")        # required in prod
ADMIN_PASSWORD = os.environ.get("MISE_ADMIN_PASSWORD", "")  # required in prod

SITE_NAME = os.environ.get("MISE_SITE_NAME", "Kevin Lee Photography")

# Studio (Phase 4) — empty means the feature is off; routes degrade gracefully
STRIPE_SECRET_KEY = os.environ.get("MISE_STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("MISE_STRIPE_WEBHOOK_SECRET", "")
GMAIL_USER = os.environ.get("MISE_GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.environ.get("MISE_GMAIL_APP_PASSWORD", "")
NOTION_TOKEN = os.environ.get("MISE_NOTION_TOKEN", "")

# Odysseus caption-drafting endpoint (Domain G slices 6b/6c). BOTH url+token must be
# set to arm the "Draft with AI" button (see caption_ai.is_enabled); either unset =
# drafting off and the button stays cleanly dormant. Odysseus owns model selection;
# Mise only POSTs context + a bearer token and reads back {"caption","model"}.
# Timeout is 210s — deliberately ABOVE Odysseus's ~180s caption budget so the ENDPOINT
# decides failure (returns a clean 502) and this synchronous client never fires first,
# orphaning an in-flight generation on mickey.
ODYSSEUS_CAPTION_URL = os.environ.get("MISE_ODYSSEUS_CAPTION_URL", "")
ODYSSEUS_CAPTION_TOKEN = os.environ.get("MISE_ODYSSEUS_CAPTION_TOKEN", "")
ODYSSEUS_TIMEOUT = int(os.environ.get("MISE_ODYSSEUS_TIMEOUT", "210"))

# studio-notify-on-reopen: best-effort push to Odysseus when a client reply
# auto-reopens a resolved video-comment thread. Both unset -> dormant, no outbound
# call. Timeout is SHORT (5s) — opposite of caption: a slow/down Odysseus must never
# stall the client's comment response, and a notify failure is swallowed, never raised.
REOPEN_NOTIFY_URL = os.environ.get("MISE_REOPEN_NOTIFY_URL", "")
REOPEN_NOTIFY_TOKEN = os.environ.get("MISE_REOPEN_NOTIFY_TOKEN", "")
REOPEN_NOTIFY_TIMEOUT = int(os.environ.get("MISE_REOPEN_NOTIFY_TIMEOUT", "5"))

# Shot-list read API (Domain F / B-Direct integration). Odysseus's preshoot_pack
# reads Mise's local shot list over GET /api/shots?session=<notion_page_id> with a
# bearer token. Empty = endpoint DISARMED: every request returns 503 (not 401), so the
# route ships dormant and only goes live once Kevin provisions MISE_SHOTS_TOKEN into
# flow's .env. This is the ONLY inbound service-bearer surface in Mise.
SHOTS_TOKEN = os.environ.get("MISE_SHOTS_TOKEN", "")

WEB_MAX_PX = int(os.environ.get("MISE_WEB_MAX_PX", "2048"))
THUMB_MAX_PX = int(os.environ.get("MISE_THUMB_MAX_PX", "480"))
JPEG_QUALITY = int(os.environ.get("MISE_JPEG_QUALITY", "85"))
VIDEO_MAX_W = int(os.environ.get("MISE_VIDEO_MAX_W", "1920"))
VIDEO_CRF = int(os.environ.get("MISE_VIDEO_CRF", "23"))

JOB_WORKERS = int(os.environ.get("MISE_JOB_WORKERS", "2"))

# Recurring-plan scheduler: how often the in-process thread sweeps for due
# retainer drafts. Generates DRAFTS only (never sends/charges). The sweep is
# idempotent, so the only effect of the interval is how soon after a restart a
# due monthly draft is caught up — an hour is plenty for a monthly event.
RECURRING_TICK_SECONDS = int(os.environ.get("MISE_RECURRING_TICK_SECONDS", "3600"))

PIN_MAX_FAILS = int(os.environ.get("MISE_PIN_MAX_FAILS", "5"))
PIN_LOCKOUT_MIN = int(os.environ.get("MISE_PIN_LOCKOUT_MIN", "15"))

# Refuse uploads when free disk drops below this (GB) — fail loud, not full.
MIN_FREE_GB = int(os.environ.get("MISE_MIN_FREE_GB", "10"))

SESSION_MAX_AGE = int(os.environ.get("MISE_SESSION_MAX_AGE", str(60 * 60 * 24 * 90)))

COOKIE_SECURE = _b("MISE_COOKIE_SECURE", "false")  # true once behind the tunnel


def ensure_dirs() -> None:
    for d in (DATA_DIR, MEDIA_DIR, ZIP_DIR, TMP_DIR, BRAND_DIR):
        d.mkdir(parents=True, exist_ok=True)
