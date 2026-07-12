"""Mounted `/api/v1` contract tests from a native-client perspective."""

import asyncio
import json
import logging

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import config, db, mobile_api, ratelimit, saas
from app.main import app

pytestmark = pytest.mark.unit


def _device() -> dict:
    return {
        "installation_id": "A8A06DC2-2034-4E3B-B07D-0CBFD2455B98",
        "name": "Kevin's iPhone",
        "platform": "ios",
        "app_version": "1.0 (42)",
    }


def _login(client: TestClient, *, password: str = "owner-password"):
    return client.post(
        "/api/v1/auth/studio/login",
        json={"email": None, "password": password, "device": _device()},
    )


def _configure_hosted(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SECRET_KEY", "mobile-hosted-secret")
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    ratelimit._hits.clear()
    saas._MIGRATED_TENANT_DBS.clear()
    saas.migrate_control()


@pytest.fixture
def mobile_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "SECRET_KEY", "mobile-api-secret")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "owner-password")
    monkeypatch.setattr(config, "BASE_URL", "https://studio.test")
    monkeypatch.setattr(config, "SITE_NAME", "North Star Studio")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "mise.db")
    ratelimit._hits.clear()
    db.migrate()
    client = TestClient(app, base_url="https://studio.test")
    yield client
    client.close()
    ratelimit._hits.clear()


def test_tenant_discovery_and_openapi_are_scoped_native_contracts(mobile_client):
    mount_root = mobile_client.get("/api/v1", follow_redirects=False)
    assert mount_root.status_code == 404
    assert "location" not in mount_root.headers
    assert mount_root.json()["code"] == "request.not_found"

    response = mobile_client.get("/api/v1/tenant")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=300"
    assert response.headers["x-request-id"].startswith("req_")
    assert response.json() == {
        "cache_namespace": response.json()["cache_namespace"],
        "slug": None,
        "studio_name": "North Star Studio",
        "canonical_base_url": "https://studio.test/",
        "brand_accent_hex": "#2F5C45",
        "time_zone": config.TIMEZONE,
        "currency_code": "USD",
        "auth_methods": ["studio_password", "shared_access"],
    }

    schema_response = mobile_client.get("/api/v1/openapi.json")
    assert schema_response.status_code == 200
    schema = schema_response.json()
    assert schema["info"]["title"] == "Mise Mobile API"
    assert set(schema["paths"]) == {
        "/devices",
        "/devices/current",
        "/tenant",
        "/auth/studio/login",
        "/auth/refresh",
        "/auth/logout",
        "/auth/sessions",
        "/auth/sessions/{session_id}",
        "/client-auth/gallery/unlock",
        "/client-auth/portal/unlock",
        "/client-auth/workspace/unlock",
        "/client-auth/document/exchange",
        "/client/portal",
        "/client/workspace",
        "/client/document",
        "/client/gallery",
        "/client/gallery/assets/{asset_id}/thumbnail",
        "/client/proposal/accept",
        "/client/proposal/decline",
        "/client/gallery/assets/{asset_id}/preview",
        "/client/gallery/assets/{asset_id}/poster",
        "/client/gallery/assets/{asset_id}/download",
        "/client/gallery/assets/{asset_id}/favorite",
        "/client/gallery/assets/{asset_id}/comments",
        "/me",
        "/dashboard",
        "/clients",
        "/clients/{client_id}",
        "/projects",
        "/projects/{project_id}",
        "/tasks",
        "/tasks/{task_id}",
        "/galleries",
        "/galleries/{gallery_id}",
        "/galleries/{gallery_id}/cull",
        "/galleries/{gallery_id}/cull/assets/{asset_id}/thumbnail",
        "/galleries/{gallery_id}/cull/assets/{asset_id}/preview",
        "/galleries/{gallery_id}/assets/{asset_id}/cull",
        "/ai/runs",
        "/content/captions",
        "/content/captions/{caption_id}",
        "/content/captions/{caption_id}/suggestions",
        "/content/captions/{caption_id}/suggestions/{suggestion_id}",
        "/event-types",
        "/bookings",
        "/bookings/{booking_id}",
        "/bookings/{booking_id}/slots",
        "/bookings/{booking_id}/cancel",
        "/bookings/{booking_id}/reschedule",
    }
    assert all("admin" not in path for path in schema["paths"])
    decision = schema["paths"]["/galleries/{gallery_id}/assets/{asset_id}/cull"]["patch"]
    decision_headers = {
        parameter["name"]: parameter
        for parameter in decision["parameters"]
        if parameter["in"] == "header"
    }
    assert set(decision_headers) == {"Authorization", "Idempotency-Key", "If-Match"}
    assert all(parameter["required"] for parameter in decision_headers.values())
    assert {"200", "401", "403", "404", "409", "422", "429"} <= set(decision["responses"])
    media = schema["paths"]["/galleries/{gallery_id}/cull/assets/{asset_id}/preview"]["get"]
    assert "image/jpeg" in media["responses"]["200"]["content"]
    assert {"200", "304", "401", "403", "404", "422", "429"} <= set(media["responses"])
    page = schema["paths"]["/galleries/{gallery_id}/cull"]["get"]
    page_headers = {
        parameter["name"]: parameter
        for parameter in page["parameters"]
        if parameter["in"] == "header"
    }
    assert set(page_headers) == {"Authorization", "If-None-Match"}
    assert page_headers["Authorization"]["required"] is True
    assert page_headers["If-None-Match"]["required"] is False
    assert {"200", "304", "401", "403", "404", "409", "422", "429"} <= set(page["responses"])
    for operation, status in ((page, "409"), (decision, "409"), (media, "422")):
        schema_ref = operation["responses"][status]["content"]["application/problem+json"][
            "schema"
        ]["$ref"]
        assert schema_ref == "#/components/schemas/APIProblem"
    retry = page["responses"]["429"]
    assert retry["headers"]["Retry-After"]["schema"] == {
        "type": "integer",
        "minimum": 0,
    }
    assert (
        retry["content"]["application/problem+json"]["schema"]["$ref"]
        == "#/components/schemas/APIProblem"
    )
    activity = schema["paths"]["/ai/runs"]["get"]
    activity_headers = {
        parameter["name"]: parameter
        for parameter in activity["parameters"]
        if parameter["in"] == "header"
    }
    assert set(activity_headers) == {"Authorization", "If-None-Match"}
    assert activity_headers["Authorization"]["required"] is True
    assert activity_headers["If-None-Match"]["required"] is False
    activity_query = {
        parameter["name"]: parameter
        for parameter in activity["parameters"]
        if parameter["in"] == "query"
    }
    assert set(activity_query) == {"cursor", "limit"}
    cursor_string = next(
        option
        for option in activity_query["cursor"]["schema"]["anyOf"]
        if option["type"] == "string"
    )
    assert cursor_string["maxLength"] == 1024
    assert activity_query["limit"]["schema"]["minimum"] == 1
    assert activity_query["limit"]["schema"]["maximum"] == 100
    assert {"200", "304", "401", "403", "422", "429", "500"} <= set(activity["responses"])
    assert (
        activity["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/AIRunPage"
    )
    for status in ("401", "403", "422", "429", "500"):
        assert (
            activity["responses"][status]["content"]["application/problem+json"]["schema"]["$ref"]
            == "#/components/schemas/APIProblem"
        )
    for status in ("200", "304"):
        response_headers = activity["responses"][status]["headers"]
        assert set(response_headers) == {"Cache-Control", "ETag", "Vary"}
        assert response_headers["Cache-Control"]["schema"]["const"] == "private, no-cache"
        assert response_headers["Vary"]["schema"]["const"] == "Authorization"
    assert activity["responses"]["429"]["headers"]["Retry-After"]["schema"] == {
        "type": "integer",
        "minimum": 0,
    }
    activity_item = schema["components"]["schemas"]["AIRunItem"]
    assert set(activity_item["properties"]) == {
        "id",
        "capability",
        "provider",
        "status",
        "review",
        "latency_ms",
        "cost_micro_usd",
        "tokens",
        "subject",
        "created_at",
    }
    assert set(activity_item["properties"]["provider"]["enum"]) == {
        "argus",
        "qwen",
        "odysseus",
        "dionysus",
        "aphrodite",
        "other",
    }
    assert set(schema["components"]["schemas"]["AIActivitySubject"]["properties"]) == {
        "kind",
    }
    content_page = schema["paths"]["/content/captions"]["get"]
    content_page_headers = {
        parameter["name"]: parameter
        for parameter in content_page["parameters"]
        if parameter["in"] == "header"
    }
    assert set(content_page_headers) == {"Authorization", "If-None-Match"}
    assert content_page_headers["Authorization"]["required"] is True
    assert content_page_headers["If-None-Match"]["required"] is False
    content_page_query = {
        parameter["name"]: parameter
        for parameter in content_page["parameters"]
        if parameter["in"] == "query"
    }
    assert set(content_page_query) == {"cursor", "limit"}
    assert {"200", "304", "401", "403", "404", "422", "429", "500"} <= set(
        content_page["responses"]
    )
    for status in ("200", "304"):
        response_headers = content_page["responses"][status]["headers"]
        assert set(response_headers) == {"Cache-Control", "ETag", "Vary"}
        assert response_headers["Cache-Control"]["schema"]["const"] == "private, no-cache"
        assert response_headers["Vary"]["schema"]["const"] == "Authorization"

    content_detail = schema["paths"]["/content/captions/{caption_id}"]["get"]
    detail_headers = {
        parameter["name"]: parameter
        for parameter in content_detail["parameters"]
        if parameter["in"] == "header"
    }
    assert set(detail_headers) == {"Authorization", "If-None-Match"}
    assert {"200", "304", "401", "403", "404", "422", "429", "500"} <= set(
        content_detail["responses"]
    )

    create_suggestion = schema["paths"]["/content/captions/{caption_id}/suggestions"]["post"]
    create_headers = {
        parameter["name"]: parameter
        for parameter in create_suggestion["parameters"]
        if parameter["in"] == "header"
    }
    assert set(create_headers) == {"Authorization", "Idempotency-Key", "If-Match"}
    assert all(parameter["required"] for parameter in create_headers.values())
    assert {"202", "401", "403", "404", "409", "422", "429", "500"} <= set(
        create_suggestion["responses"]
    )
    accepted_headers = create_suggestion["responses"]["202"]["headers"]
    assert set(accepted_headers) == {
        "Cache-Control",
        "Idempotency-Replayed",
        "Location",
        "Vary",
    }
    assert accepted_headers["Cache-Control"]["schema"]["const"] == "no-store"
    assert accepted_headers["Vary"]["schema"]["const"] == "Authorization"

    poll_suggestion = schema["paths"]["/content/captions/{caption_id}/suggestions/{suggestion_id}"][
        "get"
    ]
    poll_headers = {
        parameter["name"]: parameter
        for parameter in poll_suggestion["parameters"]
        if parameter["in"] == "header"
    }
    assert set(poll_headers) == {"Authorization"}
    assert (
        poll_suggestion["responses"]["200"]["headers"]["Cache-Control"]["schema"]["const"]
        == "no-store"
    )

    update_caption = schema["paths"]["/content/captions/{caption_id}"]["patch"]
    update_headers = {
        parameter["name"]: parameter
        for parameter in update_caption["parameters"]
        if parameter["in"] == "header"
    }
    assert set(update_headers) == {"Authorization", "Idempotency-Key", "If-Match"}
    assert all(parameter["required"] for parameter in update_headers.values())
    assert {"200", "401", "403", "404", "409", "422", "429", "500"} <= set(
        update_caption["responses"]
    )
    assert set(update_caption["responses"]["200"]["headers"]) == {
        "Cache-Control",
        "ETag",
        "Idempotency-Replayed",
        "Vary",
    }
    suggestion_properties = set(schema["components"]["schemas"]["CaptionSuggestion"]["properties"])
    assert suggestion_properties == {
        "id",
        "caption_id",
        "state",
        "review",
        "candidate_text",
        "failure_reason",
        "base_revision",
        "stale",
        "created_at",
        "expires_at",
        "completed_at",
    }
    assert suggestion_properties.isdisjoint(
        {"provider", "model", "prompt", "context", "error", "idempotency_key"}
    )


def test_owner_login_me_device_list_refresh_replay_and_logout(mobile_client):
    login = _login(mobile_client)
    assert login.status_code == 200
    assert "set-cookie" not in login.headers
    assert login.headers["cache-control"] == "no-store"
    payload = login.json()
    assert payload["token_type"] == "Bearer"
    assert payload["principal"]["kind"] == "studio_owner"
    assert payload["principal"]["scopes"] == ["studio:read", "studio:write"]
    assert payload["workspace"]["api_base_url"] == "https://studio.test/"
    assert payload["access_token"] not in repr(payload["workspace"])

    access = payload["access_token"]
    refresh = payload["refresh_token"]
    headers = {"Authorization": f"Bearer {access}"}
    current = mobile_client.get("/api/v1/me", headers=headers)
    assert current.status_code == 200
    assert "token" not in json.dumps(current.json())

    sessions = mobile_client.get("/api/v1/auth/sessions", headers=headers)
    assert sessions.status_code == 200
    listed = sessions.json()["sessions"]
    assert len(listed) == 1
    assert listed[0]["is_current"] is True
    assert listed[0]["device"] == {
        "name": "Kevin's iPhone",
        "platform": "ios",
        "app_version": "1.0 (42)",
    }
    assert "installation" not in json.dumps(listed)

    rotated = mobile_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh},
    )
    assert rotated.status_code == 200
    rotated_payload = rotated.json()
    assert rotated_payload["refresh_token"] != refresh
    assert rotated_payload["access_token"] != access
    assert rotated_payload["session_id"] == payload["session_id"]

    replay = mobile_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh},
    )
    assert replay.status_code == 401
    assert replay.headers["content-type"].startswith("application/problem+json")
    assert replay.json()["code"] == "auth.refresh_reused"
    assert replay.json()["request_id"] == replay.headers["x-request-id"]

    revoked = mobile_client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {rotated_payload['access_token']}"},
    )
    assert revoked.status_code == 401

    second_login = _login(mobile_client)
    second_access = second_login.json()["access_token"]
    logout = mobile_client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {second_access}"},
    )
    assert logout.status_code == 204
    assert logout.content == b""
    assert (
        mobile_client.get(
            "/api/v1/me",
            headers={"Authorization": f"Bearer {second_access}"},
        ).status_code
        == 401
    )


def test_api_never_uses_admin_cookie_as_bearer_authentication(mobile_client):
    browser_login = mobile_client.post(
        "/admin/login",
        data={"password": "owner-password"},
        follow_redirects=False,
    )
    assert browser_login.status_code == 303
    assert "mise_admin" in mobile_client.cookies

    response = mobile_client.get("/api/v1/me")
    assert response.status_code == 401
    assert response.json()["code"] == "auth.invalid_token"
    assert response.headers["www-authenticate"] == "Bearer"


def test_validation_problem_never_reflects_request_secrets(mobile_client):
    secret = "correct-horse-private-value"
    body = {
        "email": None,
        "password": secret,
        "device": {**_device(), "app_version": "x" * 100},
    }
    response = mobile_client.post("/api/v1/auth/studio/login", json=body)

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "request.validation_failed"
    assert response.json()["request_id"] == response.headers["x-request-id"]
    assert secret not in response.text
    assert "input" not in response.text
    assert response.json()["errors"][0]["path"] == ["body", "device", "app_version"]


def test_unhandled_api_boundary_never_logs_or_alerts_exception_values(monkeypatch, caplog):
    secret = "sensitive-response-value-must-never-reach-logs"
    alerts_sent: list[str] = []
    monkeypatch.setattr(
        mobile_api.alerts,
        "error_alert",
        lambda key, message: alerts_sent.extend((key, message)),
    )
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/test-failure",
            "query_string": b"",
            "headers": [],
            "scheme": "https",
            "server": ("studio.test", 443),
            "client": ("127.0.0.1", 50000),
        }
    )
    request.state.request_id = "req_safe_test"

    async def fail(_request):
        raise RuntimeError(secret)

    with caplog.at_level(logging.ERROR, logger="mise.mobile_api"):
        response = asyncio.run(mobile_api.contain_unhandled_errors(request, fail))

    assert response.status_code == 500
    assert json.loads(response.body)["request_id"] == "req_safe_test"
    assert secret not in caplog.text
    assert all(secret not in item for item in alerts_sent)
    assert "RuntimeError" in caplog.text


def test_outer_rate_limit_is_a_correlated_problem(mobile_client, monkeypatch):
    monkeypatch.setitem(config.RATE_LIMITS, "api", (1, 60))
    ratelimit._hits.clear()

    assert mobile_client.get("/api/v1/tenant").status_code == 200
    limited = mobile_client.get("/api/v1/tenant", headers={"Accept": "text/html"})

    assert limited.status_code == 429
    assert limited.headers["content-type"].startswith("application/problem+json")
    assert limited.headers["retry-after"]
    assert limited.json()["code"] == "request.rate_limited"
    assert limited.json()["request_id"] == limited.headers["x-request-id"]


def test_link_only_gallery_exchange_stays_resource_scoped(mobile_client):
    gallery_id = db.run(
        """INSERT INTO galleries (slug, title, pin, published, type, require_pin)
           VALUES (?,?,?,?,?,?)""",
        ("mobile-drop", "Client transfer", "unused", 1, "drop", 0),
    )
    body = {"kind": "gallery", "slug": "mobile-drop", "pin": None, "device": _device()}
    response = mobile_client.post("/api/v1/client-auth/gallery/unlock", json=body)

    assert response.status_code == 200
    principal = response.json()["principal"]
    assert principal["kind"] == "gallery_guest"
    assert principal["scopes"] == [
        f"gallery:{gallery_id}:comment",
        f"gallery:{gallery_id}:download",
        f"gallery:{gallery_id}:favorite",
        f"gallery:{gallery_id}:read",
    ]
    assert "studio:read" not in principal["scopes"]

    mismatched = mobile_client.post("/api/v1/client-auth/portal/unlock", json=body)
    assert mismatched.status_code == 422
    assert mismatched.json()["code"] == "request.kind_mismatch"


def test_unknown_hosted_tenant_is_json_problem_without_redirect(tmp_path, monkeypatch):
    _configure_hosted(tmp_path, monkeypatch)

    client = TestClient(app, base_url="https://missing.mise.test")
    response = client.get("/api/v1/tenant", follow_redirects=False)
    client.close()

    assert response.status_code == 404
    assert "location" not in response.headers
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "tenant.not_found"
    assert response.json()["request_id"] == response.headers["x-request-id"]


def test_billing_locked_owner_can_recover_session_but_feature_data_stays_blocked(
    tmp_path, monkeypatch
):
    _configure_hosted(tmp_path, monkeypatch)
    tenant = saas.create_tenant(
        "locked",
        "Locked Studio",
        "owner@locked.test",
        "hosted-password",
    )
    with saas.control_connect() as con:
        con.execute("UPDATE tenants SET plan_status='canceled' WHERE id=?", (tenant["id"],))

    client = TestClient(app, base_url="https://locked.mise.test")
    descriptor = client.get("/api/v1/tenant")
    login = client.post(
        "/api/v1/auth/studio/login",
        json={
            "email": "owner@locked.test",
            "password": "hosted-password",
            "device": _device(),
        },
    )
    access = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {access}"}
    current = client.get("/api/v1/me", headers=headers)
    blocked = client.get("/api/v1/dashboard", headers=headers)
    client.close()

    assert descriptor.status_code == 200
    assert login.status_code == 200
    assert current.status_code == 200
    assert blocked.status_code == 402
    assert blocked.headers["content-type"].startswith("application/problem+json")
    assert blocked.json()["code"] == "tenant.subscription_required"
