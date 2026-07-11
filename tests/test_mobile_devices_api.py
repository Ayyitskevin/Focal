"""Native APNs registration contract and session-binding tests."""

import base64
import json

import pytest
from fastapi.testclient import TestClient

from app import config, db, ratelimit
from app.main import app

pytestmark = pytest.mark.unit

INSTALLATION_ID = "A8A06DC2-2034-4E3B-B07D-0CBFD2455B98"
APNS_TOKEN = "ab" * 32


def _device(installation_id: str = INSTALLATION_ID) -> dict[str, str]:
    return {
        "installation_id": installation_id,
        "name": "Kevin's iPhone",
        "platform": "ios",
        "app_version": "1.0 (42)",
    }


def _registration(installation_id: str = INSTALLATION_ID) -> dict[str, object]:
    return {
        "installation_id": installation_id,
        "apns_token": APNS_TOKEN,
        "environment": "sandbox",
        "locale": "en-US",
        "app_version": "1.0 (42)",
        "preferences": {"payments": False},
    }


def _login(client: TestClient, installation_id: str = INSTALLATION_ID) -> dict:
    response = client.post(
        "/api/v1/auth/studio/login",
        json={
            "email": None,
            "password": "owner-password",
            "device": _device(installation_id),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _headers(login: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {login['access_token']}"}


@pytest.fixture
def mobile_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "SECRET_KEY", "mobile-device-api-secret")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "owner-password")
    monkeypatch.setattr(config, "BASE_URL", "https://studio.test")
    monkeypatch.setattr(config, "SITE_NAME", "North Star Studio")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "mise.db")
    monkeypatch.setattr(config, "APNS_TOPIC", "com.ayyitskevin.mise")
    monkeypatch.setattr(config, "APNS_ENVIRONMENT", "sandbox")
    monkeypatch.setattr(
        config,
        "APNS_TOKEN_ENCRYPTION_KEY",
        base64.b64encode(b"k" * 32).decode(),
    )
    ratelimit._hits.clear()
    db.migrate()
    client = TestClient(app, base_url="https://studio.test")
    yield client
    client.close()
    ratelimit._hits.clear()


def test_owner_device_registration_preference_lifecycle_and_storage(mobile_client):
    unauthenticated = mobile_client.post("/api/v1/devices", json=_registration())
    assert unauthenticated.status_code == 401

    login = _login(mobile_client)
    headers = _headers(login)
    registered = mobile_client.post(
        "/api/v1/devices",
        headers=headers,
        json=_registration(),
    )
    assert registered.status_code == 200, registered.text
    assert registered.headers["cache-control"] == "no-store"
    etag = registered.headers["etag"]
    assert etag.startswith('"device-') and etag.endswith('"')
    assert registered.json() == {
        "environment": "sandbox",
        "locale": "en-US",
        "app_version": "1.0 (42)",
        "preferences": {
            "new_bookings": True,
            "booking_changes": True,
            "proposal_responses": True,
            "payments": False,
        },
        "active": True,
        "registered_at": registered.json()["registered_at"],
        "updated_at": registered.json()["updated_at"],
    }
    assert set(registered.json()) == {
        "environment",
        "locale",
        "app_version",
        "preferences",
        "active",
        "registered_at",
        "updated_at",
    }
    assert APNS_TOKEN not in registered.text
    assert INSTALLATION_ID not in registered.text

    stored = db.one("SELECT * FROM mobile_push_devices")
    assert stored is not None
    assert stored["installation_id_hash"] != INSTALLATION_ID
    assert len(stored["installation_id_hash"]) == 64
    assert stored["token_hash"] != APNS_TOKEN
    assert len(stored["token_hash"]) == 64
    assert stored["token_ciphertext"] != APNS_TOKEN
    assert APNS_TOKEN not in stored["token_ciphertext"]

    current = mobile_client.get("/api/v1/devices/current", headers=headers)
    assert current.status_code == 200
    assert current.headers["etag"] == etag
    assert current.json() == registered.json()

    missing_precondition = mobile_client.patch(
        "/api/v1/devices/current",
        headers=headers,
        json={"preferences": {"new_bookings": False}},
    )
    assert missing_precondition.status_code == 422
    assert missing_precondition.json()["code"] == "resource.if_match_required"

    changed = mobile_client.patch(
        "/api/v1/devices/current",
        headers={**headers, "If-Match": etag},
        json={"preferences": {"new_bookings": False}},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["preferences"]["new_bookings"] is False
    assert changed.headers["etag"] != etag

    stale = mobile_client.patch(
        "/api/v1/devices/current",
        headers={**headers, "If-Match": etag},
        json={"preferences": {"booking_changes": False}},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "resource.version_conflict"

    deleted = mobile_client.delete("/api/v1/devices/current", headers=headers)
    assert deleted.status_code == 204
    assert deleted.content == b""
    deactivated = db.one("SELECT * FROM mobile_push_devices")
    assert deactivated["active"] == 0
    assert deactivated["token_ciphertext"] is None
    assert mobile_client.get("/api/v1/devices/current", headers=headers).status_code == 404
    assert mobile_client.delete("/api/v1/devices/current", headers=headers).status_code == 204


def test_device_registration_validates_binding_metadata_and_secrets(mobile_client):
    login = _login(mobile_client)
    headers = _headers(login)

    wrong_installation = mobile_client.post(
        "/api/v1/devices",
        headers=headers,
        json=_registration("b8a06dc2-2034-4e3b-b07d-0cbfd2455b99"),
    )
    assert wrong_installation.status_code == 404
    assert wrong_installation.json()["code"] == "device.not_found"

    malformed_secret = "not-a-valid-private-apns-token"
    malformed = mobile_client.post(
        "/api/v1/devices",
        headers=headers,
        json={**_registration(), "apns_token": malformed_secret},
    )
    assert malformed.status_code == 422
    assert malformed.json()["code"] == "request.validation_failed"
    assert malformed_secret not in malformed.text
    assert "input" not in malformed.text

    tenant_override = mobile_client.post(
        "/api/v1/devices",
        headers=headers,
        json={**_registration(), "tenant_id": 999},
    )
    assert tenant_override.status_code == 422

    for missing in ("locale", "app_version"):
        payload = _registration()
        del payload[missing]
        response = mobile_client.post("/api/v1/devices", headers=headers, json=payload)
        assert response.status_code == 422

    environment = mobile_client.post(
        "/api/v1/devices",
        headers=headers,
        json={**_registration(), "environment": "production"},
    )
    assert environment.status_code == 422
    assert environment.json()["code"] == "device.environment_mismatch"

    for invalid_value in ("false", 0, None):
        payload = _registration()
        payload["preferences"] = {"payments": invalid_value}
        invalid_preference = mobile_client.post(
            "/api/v1/devices",
            headers=headers,
            json=payload,
        )
        assert invalid_preference.status_code == 422
        assert invalid_preference.json()["code"] == "request.validation_failed"


def test_logout_and_credential_rotation_erase_registered_token(
    mobile_client,
    monkeypatch,
):
    first_login = _login(mobile_client)
    first_headers = _headers(first_login)
    assert (
        mobile_client.post(
            "/api/v1/devices",
            headers=first_headers,
            json=_registration(),
        ).status_code
        == 200
    )

    logout = mobile_client.post("/api/v1/auth/logout", headers=first_headers)
    assert logout.status_code == 204
    after_logout = db.one("SELECT * FROM mobile_push_devices")
    assert after_logout["active"] == 0
    assert after_logout["token_ciphertext"] is None

    second_login = _login(mobile_client)
    second_headers = _headers(second_login)
    assert (
        mobile_client.post(
            "/api/v1/devices",
            headers=second_headers,
            json=_registration(),
        ).status_code
        == 200
    )
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "rotated-owner-password")

    stale = mobile_client.get("/api/v1/me", headers=second_headers)
    assert stale.status_code == 401
    assert stale.json()["code"] == "auth.invalid_token"
    after_rotation = db.one("SELECT * FROM mobile_push_devices")
    assert after_rotation["active"] == 0
    assert after_rotation["token_ciphertext"] is None
    assert APNS_TOKEN not in json.dumps(dict(after_rotation))
