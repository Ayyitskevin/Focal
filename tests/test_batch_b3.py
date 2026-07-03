"""Batch B / Slice B3: the marketing site becomes crawlable.

Three SEO defects on the hosted platform host, found live: /robots.txt and
/sitemap.xml 303'd crawlers to /pricing (not platform paths), and the
common_headers middleware stamped X-Robots-Tag: noindex on /pricing and /demo —
silently overriding the index,follow meta those pages have declared since B1
(headers beat meta for noindex). Tenant hosts keep their studio robots rules
byte-identical via delegation.
"""

import asyncio

import pytest

from app import config, saas

pytestmark = pytest.mark.unit


def _configure_saas(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SECRET_KEY", "b3-secret")
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    saas._MIGRATED_TENANT_DBS.clear()
    saas.migrate_control()


def test_marketing_robots_and_sitemap(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    robots = asyncio.run(saas.saas_robots())
    assert "Sitemap: https://mise.test/sitemap.xml" in robots
    assert "Disallow: /admin" in robots

    sitemap = asyncio.run(saas.saas_sitemap())
    assert sitemap.media_type == "application/xml"
    body = sitemap.body.decode()
    for page in ("/pricing", "/demo", "/terms", "/privacy", "/support"):
        assert f"<loc>https://mise.test{page}</loc>" in body
    # And the middleware no longer 303s crawlers away from either file.
    assert saas._platform_path("/robots.txt") and saas._platform_path("/sitemap.xml")


def test_tenant_hosts_keep_the_studio_rules(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    with saas.tenant_runtime(tenant):
        robots = asyncio.run(saas.saas_robots())
    # The studio file, not the platform one: galleries/portal/media stay off-limits.
    assert "Disallow: /g/" in robots and "Disallow: /portal/" in robots


def test_marketing_pages_shed_the_noindex_header(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "root.db")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "op-pw")

    # The saas router isn't mounted here (app.main imported without SAAS_MODE),
    # but common_headers stamps EVERY response — including 404s — and that
    # middleware line is exactly what's under test. Route-level 200s for these
    # paths are covered by the direct-handler tests above and the live boot.
    with TestClient(app) as client:  # lifespan runs = the real middleware stack
        for path in ("/pricing", "/demo", "/robots.txt", "/sitemap.xml"):
            resp = client.get(path, headers={"Host": "mise.test"})
            assert "X-Robots-Tag" not in resp.headers, path
        # The admin surface stays noindex.
        resp = client.get("/admin/login", headers={"Host": "mise.test"})
        assert resp.headers.get("X-Robots-Tag") == "noindex, nofollow"
        # Self-hosted studio mode: /pricing isn't a studio page — noindex returns.
        monkeypatch.setattr(config, "SAAS_MODE", False)
        resp = client.get("/pricing")
        assert resp.headers.get("X-Robots-Tag") == "noindex, nofollow"
