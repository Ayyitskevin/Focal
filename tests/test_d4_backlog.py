"""D4 backlog: the lapsed-studio branded page (the one HIGH left after #131).

A client who follows a hosted studio's gallery/invoice link while that studio's
subscription has lapsed used to get a raw ``{"detail": "subscription required"}``
JSON 402 — a blob that also blamed the studio's billing. tenant_middleware now
serves a neutral branded page to browsers while keeping the JSON 402 contract for
non-browser callers (mirroring the unknown-tenant handling directly above it).

Admin owners on a lapsed studio must still be redirected to /admin/billing?expired=1
(unchanged), and the billing page now reads that flag — covered by a light check.
"""

import asyncio

import pytest
from starlette.requests import Request

from app import config, saas

pytestmark = pytest.mark.unit


def _configure_saas(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SECRET_KEY", "d4-secret")
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    saas._MIGRATED_TENANT_DBS.clear()
    saas.migrate_control()


def _lapsed(tenant_id):
    with saas.control_connect() as con:
        con.execute(
            "UPDATE tenants SET trial_ends_at=?, stripe_subscription_id=NULL WHERE id=?",
            (saas._iso(saas._now() - saas.timedelta(days=5)), tenant_id),
        )


def _req(path, host, accept):
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [(b"host", host.encode()), (b"accept", accept.encode())],
            "query_string": b"",
            "scheme": "https",
            "server": (host, 443),
            "client": ("203.0.113.7", 40000),
        }
    )


async def _boom(request):  # call_next — reaching it means we failed to short-circuit
    raise AssertionError("lapsed-studio request should have been short-circuited")


def _run(path, host, accept):
    return asyncio.run(saas.tenant_middleware(_req(path, host, accept), _boom))


def test_lapsed_studio_client_link_serves_a_branded_page_to_browsers(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    t = saas.create_tenant("aperture", "Aperture Studio", "a@example.com", "secret123")
    _lapsed(t["id"])
    assert saas.tenant_has_access(saas.tenant_by_slug("aperture")) is False

    resp = _run("/g/some-gallery", "aperture.mise.test", "text/html")
    body = resp.body.decode()
    assert resp.status_code == 402  # the status/contract is preserved
    assert "Aperture Studio" in body  # the tenant's OWN name, warmly
    assert "temporarily" in body.lower()
    # never leak the billing reason to the client, and never dump raw JSON
    assert "subscription required" not in body
    assert "subscription" not in body.lower()


def test_lapsed_studio_client_link_keeps_json_402_for_non_browsers(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    t = saas.create_tenant("aperture", "Aperture Studio", "a@example.com", "secret123")
    _lapsed(t["id"])

    resp = _run("/g/some-gallery", "aperture.mise.test", "application/json")
    assert resp.status_code == 402
    assert b"subscription required" in resp.body  # programmatic contract unchanged


def test_lapsed_studio_owner_is_still_redirected_to_billing(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    t = saas.create_tenant("aperture", "Aperture Studio", "a@example.com", "secret123")
    _lapsed(t["id"])

    resp = _run("/admin/home", "aperture.mise.test", "text/html")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/billing?expired=1"


def test_billing_page_reads_the_expired_flag(tmp_path, monkeypatch):
    """The ?expired=1 the middleware sets is now consumed into the billing context."""
    _configure_saas(tmp_path, monkeypatch)
    import inspect

    src = inspect.getsource(saas.billing)
    assert 'request.query_params.get("expired")' in src
