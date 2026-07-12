"""In-memory per-IP sliding-window rate limiter (single uvicorn worker).

Guards abuse-prone routes (downloads/ZIP builds, public form POSTs, admin) WITHOUT
touching the thumbnail grid — a gallery legitimately bursts dozens of /media/
requests on load, so those are exempt. Logged-in admins are exempt so deploys and
post-deploy testing never trip it. State is in-process: it resets on restart, which
is fine for rate limiting and avoids a DB write on every request (which would itself
be a DoS amplifier). Single worker means one shared view of the window.
"""

import logging
import time
from collections import defaultdict, deque

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from . import config, security

log = logging.getLogger("mise.ratelimit")

_hits: dict[tuple[str, str], deque] = defaultdict(deque)
_last_gc = 0.0


def _bucket_for(path: str, method: str = "GET") -> str | None:
    """Bucket name to charge, or None to skip (exempt)."""
    if path == "/healthz" or path.startswith(("/static/", "/media/", "/site/img/", "/work/")):
        return None  # static + media grid: legit bursts, never limited
    if path in ("/start-trial", "/admin/forgot", "/waitlist"):
        # Tenant provisioning / reset-email sends / waitlist joins: tight hourly
        # bucket (ADR 0050/0051) — only the POST costs anything; viewing the form
        # must not spend it.
        if method == "POST":
            return "signup"
        return "admin" if path.startswith("/admin") else None
    if path in {"/api/v1/auth/studio/login", "/api/v1/auth/refresh"} or path.startswith(
        "/api/v1/client-auth/"
    ):
        return "api_auth"
    if path.startswith("/api/v1/media/"):
        # Original-file downloads share the web download bucket; thumbnail/preview
        # variants get their own generous bucket because a native gallery grid
        # legitimately bursts dozens of authenticated media requests on open.
        return "download" if path.endswith("/download") else "api_media"
    if path == "/api/v1" or path.startswith("/api/v1/"):
        return "api"
    if "/download" in path:
        return "download"
    if path.startswith("/admin"):
        return "admin"
    if path.startswith(("/g/", "/portal/", "/i/", "/p/", "/contact", "/book", "/forms/")):
        return "public"
    return None


def _gc(now: float) -> None:
    """Drop empty/stale deques so memory stays bounded across many IPs."""
    global _last_gc
    if now - _last_gc < 300:
        return
    _last_gc = now
    for key in [k for k, dq in _hits.items() if not dq or dq[-1] < now - 3600]:
        _hits.pop(key, None)


def check(request: Request, path: str) -> Response | None:
    """Return a 429 response if over limit, else None (and record the hit)."""
    bucket = _bucket_for(path, request.method)
    if bucket is None or security.is_admin(request):
        return None
    limit, window = config.RATE_LIMITS[bucket]
    ip = security.client_ip(request)
    now = time.time()
    _gc(now)
    dq = _hits[(ip, bucket)]
    cutoff = now - window
    while dq and dq[0] < cutoff:
        dq.popleft()
    if len(dq) >= limit:
        retry = int(dq[0] + window - now) + 1
        log.warning("rate limit hit: %s bucket=%s ip=%s", path, bucket, ip)
        headers = {"Retry-After": str(retry)}
        if path == "/api/v1" or path.startswith("/api/v1/"):
            return JSONResponse(
                {
                    "type": "https://mise.example/problems/rate-limited",
                    "title": "Too many requests",
                    "status": 429,
                    "code": "request.rate_limited",
                    "detail": "Too many requests — slow down.",
                    "request_id": getattr(request.state, "request_id", None),
                    "errors": [],
                },
                status_code=429,
                headers=headers,
                media_type="application/problem+json",
            )
        if "text/html" in request.headers.get("accept", ""):
            # A person in a browser (a client on their gallery, not a script)
            # deserves the branded page, not a JSON blob at their worst moment.
            from .render import templates  # lazy: render pulls in db/urls

            return templates.TemplateResponse(
                request,
                "public/error.html",
                {"message": "That was a little too fast — give it a few seconds, then try again."},
                status_code=429,
                headers=headers,
            )
        return JSONResponse(
            {"detail": "Too many requests — slow down."},
            status_code=429,
            headers=headers,
        )
    dq.append(now)
    return None
