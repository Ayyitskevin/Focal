"""Outer-boundary invariants for the native API.

These checks intentionally exercise rules that run before the mounted API app:
tenant routing, billing recovery, and per-IP abuse buckets must not depend on a
route handler being reached.
"""

import pytest

from app import ratelimit, saas

pytestmark = pytest.mark.unit


def test_mobile_api_paths_never_redirect_to_platform_marketing() -> None:
    assert saas._platform_path("/api/v1")
    assert saas._platform_path("/api/v1/tenant")
    assert saas._platform_path("/api/v1/auth/studio/login")


def test_billing_lock_only_allows_mobile_session_recovery() -> None:
    allowed = {
        "/api/v1/tenant",
        "/api/v1/auth/studio/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/logout",
        "/api/v1/me",
        "/api/v1/auth/sessions",
        "/api/v1/auth/sessions/session_123",
    }
    assert all(saas._billing_allowed_path(path) for path in allowed)

    assert not saas._billing_allowed_path("/api/v1/dashboard")
    assert not saas._billing_allowed_path("/api/v1/client-auth/gallery/unlock")
    assert not saas._billing_allowed_path("/api/v1/galleries/17")


def test_mobile_credential_exchanges_have_a_dedicated_rate_bucket() -> None:
    auth_paths = {
        "/api/v1/auth/studio/login",
        "/api/v1/auth/refresh",
        "/api/v1/client-auth/gallery/unlock",
        "/api/v1/client-auth/portal/unlock",
        "/api/v1/client-auth/workspace/unlock",
        "/api/v1/client-auth/document/exchange",
    }
    assert all(ratelimit._bucket_for(path, "POST") == "api_auth" for path in auth_paths)

    assert ratelimit._bucket_for("/api/v1/tenant", "GET") == "api"
    assert ratelimit._bucket_for("/api/v1/me", "GET") == "api"
    assert ratelimit._bucket_for("/api/v1/auth/logout", "POST") == "api"


def test_mobile_gallery_derivatives_use_high_capacity_bucket_and_originals_stay_limited() -> None:
    base = "/api/v1/client/gallery/assets/17"
    for variant in ("thumbnail", "preview", "poster"):
        assert ratelimit._bucket_for(f"{base}/{variant}", "GET") == "api_media"
    assert ratelimit._bucket_for(f"{base}/download", "GET") == "download"
    assert ratelimit._bucket_for(f"{base}/favorite", "PUT") == "api"
    assert ratelimit._bucket_for(f"{base}/comments", "POST") == "api"
