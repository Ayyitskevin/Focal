"""Batch A / Slice A1: in-product feedback & help seam.

The beta promise is "every confusion becomes copy, onboarding, or a blocker" —
before this slice a confused studio owner had NO path to the operator from
inside the product (the support email lived only on public marketing pages).
These tests pin the seam: tenant submits → control-DB row + operator Telegram
ping → operator console renders it, tenant-attributed.
"""

import asyncio

import pytest
from starlette.requests import Request

from app import alerts, config, saas, security

pytestmark = pytest.mark.unit


def _configure_saas(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SECRET_KEY", "a1-secret")
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    saas._MIGRATED_TENANT_DBS.clear()
    saas.migrate_control()


def _request(path, host, cookie="", method="GET"):
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": b"",
            "headers": [
                (b"host", host.encode()),
                (b"accept", b"text/html"),
                (b"cookie", cookie.encode()),
            ],
            "scheme": "https",
            "server": (host, 443),
            "client": ("127.0.0.1", 50000),
        }
    )


def _tenant_cookie(tenant) -> str:
    fp = security._pw_fp(tenant.get("admin_password_hash") or "")
    principal = f"tenant:{tenant['id']}:{tenant['slug']}:{fp}"
    return f"{security.ADMIN_COOKIE}={security.sign(principal)}"


def _operator_cookie() -> str:
    principal = f"operator:{security._pw_fp(config.ADMIN_PASSWORD)}"
    return f"{security.ADMIN_COOKIE}={security.sign(principal)}"


def test_feedback_submit_records_row_and_pings_operator(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    pings = []
    monkeypatch.setattr(alerts, "notify", lambda text: pings.append(text))

    with saas.tenant_runtime(tenant):
        req = _request("/admin/help/feedback", "alpha.mise.test", _tenant_cookie(tenant), "POST")
        resp = asyncio.run(
            saas.tenant_feedback_submit(
                req, message="The invoice form confused me", page="/admin/studio"
            )
        )

    assert resp.status_code == 303 and resp.headers["location"] == "/admin/help?sent=1"
    rows = saas.recent_tenant_feedback()
    assert len(rows) == 1
    assert rows[0]["message"] == "The invoice form confused me"
    assert rows[0]["slug"] == "alpha" and rows[0]["studio_name"] == "Alpha Studio"
    assert rows[0]["page"] == "/admin/studio"
    # The operator hears about it (their own Telegram — a business event, not a log).
    assert len(pings) == 1 and "Alpha Studio" in pings[0] and "confused" in pings[0]


def test_blank_feedback_stores_nothing(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    with saas.tenant_runtime(tenant):
        req = _request("/admin/help/feedback", "alpha.mise.test", _tenant_cookie(tenant), "POST")
        resp = asyncio.run(saas.tenant_feedback_submit(req, message="   \n  ", page=""))
    assert resp.status_code == 303
    assert saas.recent_tenant_feedback() == []


def test_feedback_is_truncated_at_the_cap(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    monkeypatch.setattr(alerts, "notify", lambda text: None)
    with saas.tenant_runtime(tenant):
        req = _request("/admin/help/feedback", "alpha.mise.test", _tenant_cookie(tenant), "POST")
        asyncio.run(saas.tenant_feedback_submit(req, message="x" * 5000, page="y" * 900))
    row = saas.recent_tenant_feedback()[0]
    assert len(row["message"]) == saas.FEEDBACK_MAX_CHARS
    assert len(row["page"]) == 200


def test_help_page_renders_support_paths(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SAAS_SUPPORT_EMAIL", "help@mise.test")
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    with saas.tenant_runtime(tenant):
        resp = asyncio.run(
            saas.tenant_help(_request("/admin/help", "alpha.mise.test", _tenant_cookie(tenant)))
        )
    assert resp.status_code == 200
    body = resp.body.decode()
    # Support is finally discoverable while logged in: page link, email, and the form.
    assert "https://mise.test/support" in body
    assert "help@mise.test" in body
    assert 'action="/admin/help/feedback"' in body
    assert "alpha@example.com" in body  # "replies go to" line


def test_help_is_hosted_tenant_only(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    # Platform/root context (no tenant): 404 — the operator console is their surface.
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        asyncio.run(saas.tenant_help(_request("/admin/help", "mise.test", _operator_cookie())))
    assert exc.value.status_code == 404


def test_pristine_hosted_boot_operator_login_does_not_500(tmp_path, monkeypatch):
    """Launch-blocking regression caught by this slice's live boot: hosted startup
    migrated only the control DB, so the ROOT-host operator login — whose lockout
    check reads pin_attempts from the DEFAULT DB — crashed with 'no such table' on
    a pristine deploy. CI never saw it because conftest migrates the default DB.
    The app lifespan must migrate the default DB in hosted mode too."""
    from fastapi.testclient import TestClient

    from app import ratelimit
    from app.main import app

    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "pristine-root.db")  # never migrated
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "op-pw")
    ratelimit._hits.clear()
    with TestClient(app) as client:  # lifespan runs = real startup path
        r = client.post("/admin/login", data={"password": "wrong"}, headers={"host": "mise.test"})
        assert r.status_code == 401  # was 500: OperationalError on pin_attempts
        ok = client.post(
            "/admin/login",
            data={"password": "op-pw"},
            headers={"host": "mise.test"},
            follow_redirects=False,
        )
        assert ok.status_code == 303
    ratelimit._hits.clear()


def test_operator_console_shows_tenant_feedback(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "op-pw")
    monkeypatch.setattr(alerts, "notify", lambda text: None)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    saas.record_tenant_feedback(tenant["id"], "/admin/studio", "Where do retainers live?")

    resp = asyncio.run(
        saas.operator_console(_request("/admin/saas", "mise.test", _operator_cookie()))
    )
    assert resp.status_code == 200
    body = resp.body.decode()
    assert "Where do retainers live?" in body
    assert "Alpha Studio" in body
