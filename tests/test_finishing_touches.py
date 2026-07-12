"""Finishing-touches review pass: departed rows stop haunting operator KPIs.

A deleted studio's row survives on purpose (ADR 0051: billing linkage, C4 exit
reasons) — but it was still being COUNTED: every deletion permanently inflated
the Studios total and the attention/support queue (tombstones read as
'canceled'), dragged the growth rates down with zero-score ghosts, and rendered
deleted studio identities in the console table and CSV. Departed studios are now a
separate count, visible but out of every live metric.
"""

import pytest

from app import config, saas

pytestmark = pytest.mark.unit


def _configure_saas(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SECRET_KEY", "ft-secret")
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    saas._MIGRATED_TENANT_DBS.clear()
    saas.migrate_control()


def test_departed_studios_leave_every_live_metric(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    doomed = saas.create_tenant("beta", "Beta Studio", "beta@example.com", "secret123")
    before = saas.operator_tenant_overview()["counts"]
    assert before["total"] == 2 and before["departed"] == 0

    saas.delete_tenant_studio(doomed)
    counts = saas.operator_tenant_overview()["counts"]
    assert counts["total"] == 1  # not 2: the deleted row is no longer a studio
    assert counts["departed"] == 1  # ...but the departure is still visible
    assert counts["attention"] == 0  # a deletion is not a support case forever
    # The live console table and CSV omit deleted studio identities.
    overview = saas.operator_tenant_overview()
    slugs = [row["tenant"]["slug"] for row in overview["rows"]]
    assert slugs == ["alpha"]
    assert "beta-deleted" not in saas.operator_tenant_export_csv(overview)


def test_digest_headline_ignores_tombstones_but_reports_the_departure(tmp_path, monkeypatch):
    from app import mailer

    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SAAS_SUPPORT_EMAIL", "operator@example.com")
    saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    doomed = saas.create_tenant("beta", "Beta Studio", "beta@example.com", "secret123")
    saas.delete_tenant_studio(doomed)

    sent = []
    monkeypatch.setattr(mailer, "configured", lambda: True)
    monkeypatch.setattr(mailer, "send", lambda to, subject, body, **kw: sent.append(body))
    assert saas.weekly_digest_sweep() == 1
    body = sent[0]
    assert "1 studio:" in body  # headline counts the living
    assert "New studios: 2 (departures: 1)" in body  # the week tells the truth


def test_non_ascii_credentials_fail_closed_not_500(monkeypatch):
    """compare_digest raises TypeError on non-ASCII str — every comparison site
    now encodes first, so a smart-quote in a password/PIN/invite code is a plain
    auth failure, never an unhandled 500 (which also skipped the lockout charge)."""
    from app import security

    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "op-pw")
    assert security.check_admin_password("pässword") is False
    assert security.check_admin_password("op-pw") is True
    assert security.pin_matches("pïn1", "1234") is False
    assert security.pin_matches("1234", "1234") is True


def test_non_ascii_invite_code_is_403_not_500(tmp_path, monkeypatch):
    import asyncio

    from starlette.requests import Request

    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SAAS_INVITE_CODE", "sesame")
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/start-trial",
            "query_string": b"",
            "headers": [(b"host", b"mise.test"), (b"accept", b"text/html")],
            "scheme": "https",
            "server": ("mise.test", 443),
            "client": ("127.0.0.1", 50000),
        }
    )
    resp = asyncio.run(
        saas.start_trial(
            request,
            studio_name="S",
            owner_email="s@example.com",
            slug="s-studio",
            password="spw12345",
            signup_source=None,
            signup_campaign=None,
            signup_referrer=None,
            invite_code="“sesame”",  # pasted smart quotes
        )
    )
    assert resp.status_code == 403
    assert saas.tenant_by_slug("s-studio") is None


def test_hosted_from_header_survives_hostile_studio_name(monkeypatch):
    """sender_name() is tenant input in hosted mode — a control char in the studio
    name must not make EmailMessage raise at send time (ADR 0061 class)."""
    from app import mailer

    monkeypatch.setattr(mailer, "sender_name", lambda: "Evil\r\nBcc: spam@x")
    monkeypatch.setattr(config, "GMAIL_USER", "studio@example.com")
    msg = mailer._build_message("to@example.com", "Hi", "body")
    assert "\n" not in msg["From"] and "Bcc" not in dict(msg)
    assert "EvilBcc: spam@x" in msg["From"]  # neutered, not lost


def test_tenant_robots_and_sitemap_point_at_the_tenant_host(tmp_path, monkeypatch):
    import asyncio

    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    with saas.tenant_runtime(tenant):
        robots = asyncio.run(saas.saas_robots())
        sitemap = asyncio.run(saas.saas_sitemap()).body.decode()
    # The studio's own host, not the platform operator's domain.
    assert "Sitemap: https://alpha.mise.test/sitemap.xml" in robots
    assert "<loc>https://alpha.mise.test/</loc>" in sitemap
    assert "<loc>https://mise.test/" not in sitemap


def test_500_responses_carry_security_headers(monkeypatch):
    """The Exception handler runs on ServerErrorMiddleware, outside common_headers —
    it must set the security/noindex headers itself."""
    import asyncio

    from starlette.requests import Request

    from app import alerts
    from app.main import unhandled_errors

    monkeypatch.setattr(alerts, "error_alert", lambda *a, **kw: None)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/boom",
            "query_string": b"",
            "headers": [(b"host", b"mise.test"), (b"accept", b"text/html")],
            "scheme": "https",
            "server": ("mise.test", 443),
            "client": ("127.0.0.1", 50000),
        }
    )
    resp = asyncio.run(unhandled_errors(request, RuntimeError("boom")))
    assert resp.status_code == 500
    assert resp.headers["X-Robots-Tag"] == "noindex, nofollow"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in resp.headers["Content-Security-Policy"]


def test_middleware_order_puts_tenant_context_outside_the_rate_limiter():
    """Starlette runs the LAST-registered middleware outermost. tenant_context must
    execute before rate_limit (admin exemption needs the tenant principal) but
    after common_headers (short-circuited 402/404s still need headers)."""
    from app.main import app

    order = [m.kwargs["dispatch"].__name__ for m in app.user_middleware]
    # user_middleware is most-recently-registered first == execution order.
    assert order == ["common_headers", "tenant_context", "csrf_guard", "rate_limit"]
