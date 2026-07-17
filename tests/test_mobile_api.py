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
        # Self-hosted studios have no platform signup or hosted billing panel.
        "signup_url": None,
        "manage_billing_url": None,
    }

    schema_response = mobile_client.get("/api/v1/openapi.json")
    assert schema_response.status_code == 200
    schema = schema_response.json()
    assert schema["info"]["title"] == "Mise Mobile API"
    assert set(schema["paths"]) == {
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
        "/me",
        "/dashboard",
        "/clients",
        "/projects",
        "/galleries",
        "/galleries/{gallery_id}",
        "/galleries/{gallery_id}/assets/{asset_id}/favorite",
        "/tasks/{task_id}/completion",
        "/event-types",
        "/event-types/{event_type_id}/slots",
        "/bookings",
        "/bookings/{booking_id}/cancel",
        "/bookings/{booking_id}/reschedule",
        "/booking-workflows/{workflow_id}",
        "/booking-workflows/{workflow_id}/retry",
        "/client/home",
        "/client/galleries",
        "/client/galleries/{gallery_id}",
        "/client/bookings",
        "/projects/{project_id}/proposals",
        "/projects/{project_id}/contracts",
        "/projects/{project_id}/invoices",
        "/projects/{project_id}/closeout",
        "/companies",
        "/commercial/actions",
        "/companies/{company_id}/next-actions",
        "/companies/{company_id}/ar-chase",
        "/media/galleries/{gallery_id}/assets/{asset_id}/{variant}",
    }
    assert all("admin" not in path for path in schema["paths"])

    slots = schema["paths"]["/event-types/{event_type_id}/slots"]["get"]
    slot_parameters = {parameter["name"]: parameter for parameter in slots["parameters"]}
    assert slot_parameters["event_type_id"]["required"] is True
    assert slot_parameters["event_type_id"]["schema"]["minimum"] == 1
    assert slot_parameters["event_type_id"]["schema"]["maximum"] == 2**63 - 1
    assert slot_parameters["day"]["required"] is True
    assert slot_parameters["day"]["schema"]["format"] == "date"
    assert slot_parameters["reschedule_booking_id"]["required"] is False
    assert slot_parameters["reschedule_booking_id"]["schema"]["anyOf"][0]["minimum"] == 1
    assert slot_parameters["reschedule_booking_id"]["schema"]["anyOf"][0]["maximum"] == 2**63 - 1
    assert slots["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/EventTypeSlots"
    }
    slots_schema = schema["components"]["schemas"]["EventTypeSlots"]
    assert set(slots_schema["required"]) == {"event_type_id", "day", "time_zone", "slots"}
    assert slots_schema["additionalProperties"] is False
    assert slots_schema["properties"]["slots"]["maxItems"] == 2048
    assert {"401", "403", "404", "409", "422", "429"} <= set(slots["responses"])

    reschedule = schema["paths"]["/bookings/{booking_id}/reschedule"]["post"]
    idempotency_header = next(
        parameter
        for parameter in reschedule["parameters"]
        if parameter["in"] == "header" and parameter["name"] == "Idempotency-Key"
    )
    assert idempotency_header["required"] is True
    assert idempotency_header["schema"]["format"] == "uuid"
    assert reschedule["requestBody"]["required"] is True
    assert reschedule["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/BookingRescheduleRequest"
    }
    request_schema = schema["components"]["schemas"]["BookingRescheduleRequest"]
    assert set(request_schema["required"]) == {"start_at", "time_zone"}
    assert request_schema["additionalProperties"] is False
    assert reschedule["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/BookingRescheduleResult"
    }
    result_schema = schema["components"]["schemas"]["BookingRescheduleResult"]
    assert set(result_schema["required"]) == {
        "status",
        "workflow_id",
        "delivery_status",
        "original_booking_id",
        "replacement_booking_id",
        "start_at",
        "end_at",
    }
    assert result_schema["properties"]["status"]["const"] == "rescheduled"
    assert result_schema["properties"]["workflow_id"]["format"] == "uuid"
    assert result_schema["properties"]["delivery_status"]["const"] == "pending"
    assert {"MobileBearer": []} in reschedule["security"]
    bearer_scheme = schema["components"]["securitySchemes"]["MobileBearer"]
    assert bearer_scheme["type"] == "http"
    assert bearer_scheme["scheme"] == "bearer"
    assert bearer_scheme["bearerFormat"] == "opaque"
    assert {"401", "403", "404", "409", "422", "429", "503"} <= set(reschedule["responses"])
    for status in ("401", "403", "404", "409", "422", "429", "503"):
        assert set(reschedule["responses"][status]["content"]) == {"application/problem+json"}
        assert reschedule["responses"][status]["content"]["application/problem+json"]["schema"] == {
            "$ref": "#/components/schemas/APIProblem"
        }


def test_owner_login_me_device_list_refresh_replay_and_logout(mobile_client):
    login = _login(mobile_client)
    assert login.status_code == 200
    assert "set-cookie" not in login.headers
    assert login.headers["cache-control"] == "no-store"
    payload = login.json()
    assert payload["token_type"] == "Bearer"
    assert payload["principal"]["kind"] == "studio_owner"
    assert payload["principal"]["scopes"] == ["studio:read", "studio:write"]
    assert payload["available_commands"] == []
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
    # Hosted descriptors carry the funnel links the app uses instead of
    # hardcoding web-admin paths (ADR 0070): platform signup + this studio's
    # billing panel — exactly what a billing-locked owner needs to recover.
    assert descriptor.json()["signup_url"] == "https://mise.test/pricing"
    assert descriptor.json()["manage_billing_url"] == "https://locked.mise.test/admin/billing"
    assert login.status_code == 200
    assert current.status_code == 200
    assert blocked.status_code == 402
    assert blocked.headers["content-type"].startswith("application/problem+json")
    assert blocked.json()["code"] == "tenant.subscription_required"
