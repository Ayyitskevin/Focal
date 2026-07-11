"""Apple associated-domain contract tests."""

import asyncio

import pytest
from fastapi import HTTPException

from app import associated_domains, config, saas

pytestmark = pytest.mark.unit


def test_aasa_is_fail_closed_without_a_signed_application_identity(monkeypatch):
    monkeypatch.setattr(config, "APNS_TEAM_ID", "")
    monkeypatch.setattr(config, "APNS_TOPIC", "")

    with pytest.raises(HTTPException) as caught:
        associated_domains.document()

    assert caught.value.status_code == 404


def test_aasa_allows_only_typed_app_and_exact_shared_paths(monkeypatch):
    monkeypatch.setattr(config, "APNS_TEAM_ID", "A1B2C3D4E5")
    monkeypatch.setattr(config, "APNS_TOPIC", "com.ayyitskevin.mise")

    response = asyncio.run(associated_domains.apple_app_site_association())
    payload = response.body.decode()
    document = associated_domains.document()
    details = document["applinks"]["details"][0]
    components = details["components"]

    assert response.status_code == 200
    assert response.media_type == "application/json"
    assert response.headers["cache-control"] == "public, max-age=3600"
    assert "location" not in response.headers
    assert details["appIDs"] == ["A1B2C3D4E5.com.ayyitskevin.mise"]
    assert components[0]["/"] == "/app/*"
    for prefix in ("g", "portal", "w", "p", "c", "i"):
        exclusion = next(item for item in components if item.get("/") == f"/{prefix}/*/*")
        inclusion = next(item for item in components if item.get("/") == f"/{prefix}/*")
        assert exclusion["exclude"] is True
        assert components.index(exclusion) < components.index(inclusion)
    assert "token" not in payload.casefold()


def test_aasa_paths_bypass_platform_redirect_and_billing_lock():
    paths = {
        "/.well-known/apple-app-site-association",
        "/apple-app-site-association",
    }
    assert all(saas._platform_path(path) for path in paths)
    assert all(saas._billing_allowed_path(path) for path in paths)
    assert saas._billing_allowed_path("/api/v1/devices/current")
