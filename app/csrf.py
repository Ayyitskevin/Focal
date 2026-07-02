"""Same-origin enforcement for state-changing requests — CSRF defense-in-depth.

Cookie-authenticated POSTs already lean on SameSite=lax. This adds the second,
explicit layer most frameworks ship: reject an unsafe-method request whose
Origin (or, as a fallback, Referer) names a DIFFERENT site than our own.

We reject ONLY on a present-and-mismatched header. A request with neither header
is allowed through — so server-to-server webhooks (no Origin, and HMAC-verified
in their own handlers), curl, and the test client are unaffected. The real attack
this stops is a malicious page auto-submitting a form to us: the browser stamps it
with `Origin: https://evil.example`, which mismatches and gets a 403. This can only
tighten an existing legitimate flow, never break one (R3, R12).

Self-hosted mode compares against config.BASE_URL — the PUBLIC origin — not the
request's peer, because behind the Cloudflare tunnel the peer is localhost while
the browser's Origin is https://kleephotography.com. Hosted SaaS mode compares
against the incoming Host plus X-Forwarded-Proto so tenant subdomains remain
same-origin without widening the check to every tenant.
"""

import logging
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse

from . import config, urls

log = logging.getLogger("mise.csrf")

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def _origin(url: str) -> str | None:
    """Normalize a URL to scheme://host[:port], or None if it has no usable origin."""
    if not url:
        return None
    s = urlsplit(url)
    if not s.scheme or not s.hostname:
        return None
    netloc = s.hostname + (f":{s.port}" if s.port else "")
    return f"{s.scheme}://{netloc}".lower()


def check(request: Request) -> JSONResponse | None:
    """Return a 403 response for a cross-origin state-changing request, else None."""
    if request.method in _SAFE_METHODS:
        return None
    ours = urls.origin_from_url(
        urls.public_base_url(request) if config.SAAS_MODE else config.BASE_URL
    )
    # The origin the request actually ARRIVED on (Host + forwarded proto) is
    # same-origin in every sense that matters: the browser's Origin header names
    # the URL in its address bar, and a cross-site attacker's page can never make
    # that equal our own Host. Without this, single-tenant compared against
    # BASE_URL alone, so a browser reaching the app any other way — LAN IP,
    # 127.0.0.1, a fresh Docker boot before MISE_BASE_URL is set — had EVERY form
    # POST rejected: admin login included, client PIN entry included. curl and
    # the test client send no Origin, which is why no test ever saw it.
    arrived = urls.origin_from_url(urls.request_origin(request))
    sent = _origin(request.headers.get("origin", "")) or _origin(request.headers.get("referer", ""))
    if sent is None or sent == ours or sent == arrived:
        return None
    log.warning(
        "cross-origin %s %s blocked: origin=%s expected=%s",
        request.method,
        request.url.path,
        sent,
        ours,
    )
    return JSONResponse({"detail": "cross-origin request blocked"}, status_code=403)
