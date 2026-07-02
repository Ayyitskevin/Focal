"""Polish Slice 1: the launch-blocking CSRF/origin fix + browser-grade 429.

The load-bearing finding (caught by a real-browser screenshot audit, invisible
to curl/TestClient because neither sends Origin): single-tenant CSRF compared
the browser's Origin against config.BASE_URL alone, so a browser reaching the
app on any other host — LAN IP, 127.0.0.1, a fresh Docker boot before
MISE_BASE_URL is set — had EVERY form POST rejected: admin login included,
client PIN entry included. The fix accepts the origin the request actually
arrived on (Host + forwarded proto); a cross-site attacker's page can never
make its Origin equal our own Host, so the CSRF property is intact.
"""

import logging

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import config, csrf, ratelimit
from app.main import app

pytestmark = pytest.mark.unit

log = logging.getLogger(__name__)


def _request(host: str, origin: str | None, path: str = "/admin/login") -> Request:
    headers = [(b"host", host.encode()), (b"accept", b"text/html")]
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "query_string": b"",
            "headers": headers,
            "scheme": "http",
            "server": (host.split(":")[0], 80),
            "client": ("203.0.113.7", 50000),
        }
    )


# --- CSRF: same-origin by arrival host, not just BASE_URL --------------------


def test_browser_on_unconfigured_host_can_post(monkeypatch):
    # BASE_URL says localhost:8400; the browser is on the machine's LAN IP.
    # Origin == the Host the request arrived on → same-origin → allowed.
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "BASE_URL", "http://localhost:8400")
    req = _request("192.168.1.50:8400", "http://192.168.1.50:8400")
    assert csrf.check(req) is None


def test_cross_site_origin_is_still_blocked(monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "BASE_URL", "http://localhost:8400")
    req = _request("192.168.1.50:8400", "https://evil.example")
    resp = csrf.check(req)
    assert resp is not None and resp.status_code == 403


def test_configured_base_url_origin_still_allowed(monkeypatch):
    # Behind a proxy that rewrites Host, the public BASE_URL origin must keep working.
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "BASE_URL", "https://kleephotography.com")
    req = _request("localhost:8400", "https://kleephotography.com")
    assert csrf.check(req) is None


def test_headerless_post_is_unaffected(monkeypatch):
    # curl / server-to-server webhooks send no Origin — unchanged behavior.
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "BASE_URL", "http://localhost:8400")
    assert csrf.check(_request("192.168.1.50:8400", None)) is None


def test_admin_login_form_posts_from_a_real_browser(monkeypatch):
    # End-to-end through the app: a browser-stamped Origin on the login POST must
    # reach the handler (401 wrong-password), not die at the middleware (403).
    monkeypatch.setattr(config, "BASE_URL", "http://localhost:8400")
    ratelimit._hits.clear()
    with TestClient(app) as client:
        r = client.post(
            "/admin/login",
            data={"password": "nope"},
            headers={"origin": "http://testserver"},
        )
    assert r.status_code == 401  # the CSRF guard would have made this 403
    ratelimit._hits.clear()


# --- rate limiter: browsers get the branded page, scripts get JSON -----------


def test_rate_limited_browser_gets_branded_html_page(monkeypatch):
    monkeypatch.setitem(config.RATE_LIMITS, "public", (1, 60))
    ratelimit._hits.clear()
    req = _request("localhost:8400", None, path="/g/somegallery")
    assert ratelimit.check(req, "/g/somegallery") is None  # first hit records
    resp = ratelimit.check(req, "/g/somegallery")  # second trips
    assert resp is not None and resp.status_code == 429
    assert resp.headers["retry-after"]
    assert "text/html" in resp.headers["content-type"]
    assert b"too fast" in resp.body


def test_rate_limited_script_still_gets_json(monkeypatch):
    monkeypatch.setitem(config.RATE_LIMITS, "public", (1, 60))
    ratelimit._hits.clear()
    req = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/g/somegallery",
            "query_string": b"",
            "headers": [(b"host", b"localhost:8400"), (b"accept", b"application/json")],
            "scheme": "http",
            "server": ("localhost", 80),
            "client": ("203.0.113.7", 50000),
        }
    )
    assert ratelimit.check(req, "/g/somegallery") is None  # first hit records
    resp = ratelimit.check(req, "/g/somegallery")  # second trips
    assert resp is not None and resp.status_code == 429
    assert "application/json" in resp.headers["content-type"]
    ratelimit._hits.clear()
