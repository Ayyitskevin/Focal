"""Focused contract tests for the mobile API v1 authentication schemas."""

import json
from datetime import datetime

import pytest
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError

from app.mobile_api_schemas import (
    APIProblem,
    AuthSession,
    CurrentSession,
    DeviceContext,
    DeviceSummary,
    Principal,
    RefreshTokenRequest,
    SessionListResponse,
    SessionSummary,
    SharedAccessUnlockRequest,
    StudioLoginRequest,
    TenantDescriptor,
    WorkspaceContext,
)

pytestmark = pytest.mark.unit


def _device_payload() -> dict:
    return {
        "installation_id": "0A90B42C-2E61-4C8D-B85A-CA611B8F3A3C",
        "name": "Kevin's iPhone",
        "platform": "ios",
        "app_version": "1.0 (42)",
    }


def _workspace() -> WorkspaceContext:
    return WorkspaceContext(
        cache_namespace="tenant_42",
        slug="north-star-photo",
        display_name="North Star Photo",
        api_base_url="https://north-star.mise.example",
        brand_accent_hex="#2F5C45",
        time_zone="America/New_York",
        currency_code="usd",
    )


def _principal() -> Principal:
    return Principal(
        id="studio_owner",
        kind="studio_owner",
        display_name="North Star Photo",
        email="owner@example.com",
        scopes=["studio:read", "studio:write"],
    )


def test_tenant_descriptor_uses_the_swift_snake_case_wire_shape():
    descriptor = TenantDescriptor(
        cache_namespace="tenant_42",
        slug="north-star-photo",
        studio_name="North Star Photo",
        canonical_base_url="https://north-star.mise.example",
        brand_accent_hex="#2F5C45",
        time_zone="America/New_York",
        currency_code="usd",
        auth_methods=["studio_password", "shared_access"],
    )

    payload = descriptor.model_dump(mode="json")
    assert set(payload) == {
        "cache_namespace",
        "slug",
        "studio_name",
        "canonical_base_url",
        "brand_accent_hex",
        "time_zone",
        "currency_code",
        "auth_methods",
    }
    assert payload["currency_code"] == "USD"
    assert payload["canonical_base_url"].rstrip("/") == "https://north-star.mise.example"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("brand_accent_hex", "green"),
        ("time_zone", "Not/A-Time-Zone"),
        ("currency_code", "US"),
    ],
)
def test_tenant_descriptor_rejects_invalid_public_metadata(field, value):
    payload = {
        "cache_namespace": "tenant_42",
        "studio_name": "North Star Photo",
        "canonical_base_url": "https://north-star.mise.example",
        "brand_accent_hex": "#2F5C45",
        "time_zone": "America/New_York",
        "currency_code": "USD",
        "auth_methods": [],
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        TenantDescriptor.model_validate(payload)


def test_login_and_unlock_requests_bound_device_fields_and_redact_secrets():
    login = StudioLoginRequest(
        email="",
        password="correct horse battery staple",
        device=_device_payload(),
    )
    unlock = SharedAccessUnlockRequest(
        kind="gallery",
        slug="Q6YABC",
        pin="0427",
        device=_device_payload(),
    )
    refresh = RefreshTokenRequest(refresh_token="refresh-secret")

    assert login.email is None
    assert login.password.get_secret_value() == "correct horse battery staple"
    assert unlock.pin is not None and unlock.pin.get_secret_value() == "0427"
    assert refresh.refresh_token.get_secret_value() == "refresh-secret"
    assert "correct horse" not in login.model_dump_json()
    assert "0427" not in unlock.model_dump_json()
    assert "refresh-secret" not in refresh.model_dump_json()
    assert "**********" in login.model_dump_json()

    with pytest.raises(ValidationError):
        SharedAccessUnlockRequest(
            kind="gallery", slug="Q6YABC", pin="12ab", device=_device_payload()
        )
    with pytest.raises(ValidationError):
        DeviceContext(**{**_device_payload(), "app_version": "x" * 65})


def test_link_only_shared_access_allows_an_absent_pin():
    request = SharedAccessUnlockRequest(
        kind="gallery",
        slug="link-only-gallery",
        pin=None,
        device=_device_payload(),
    )
    assert request.pin is None


def test_auth_session_matches_swift_and_fastapi_encodes_rfc3339_utc():
    session = AuthSession(
        access_token="access-secret",
        refresh_token="refresh-secret",
        token_type="Bearer",
        access_token_expires_at="2026-07-10T18:45:00-04:00",
        refresh_token_expires_at="2026-08-09T22:30:00Z",
        workspace=_workspace(),
        principal=_principal(),
        available_commands=[],
        session_id="session_01J",
    )

    payload = jsonable_encoder(session)
    assert payload["access_token_expires_at"] == "2026-07-10T22:45:00Z"
    assert payload["refresh_token_expires_at"] == "2026-08-09T22:30:00Z"
    assert payload["token_type"] == "Bearer"
    assert payload["principal"]["kind"] == "studio_owner"
    assert set(AuthSession.model_json_schema()["required"]) == {
        "access_token",
        "token_type",
        "access_token_expires_at",
        "workspace",
        "principal",
        "available_commands",
    }


def test_auth_session_rejects_naive_or_backwards_expirations():
    base = {
        "access_token": "access-secret",
        "refresh_token": "refresh-secret",
        "token_type": "Bearer",
        "workspace": _workspace(),
        "principal": _principal(),
        "available_commands": [],
    }
    with pytest.raises(ValidationError, match="UTC offset"):
        AuthSession(
            **base,
            access_token_expires_at=datetime(2026, 7, 10, 22, 45),
            refresh_token_expires_at="2026-08-09T22:30:00Z",
        )
    with pytest.raises(ValidationError, match="cannot expire before"):
        AuthSession(
            **base,
            access_token_expires_at="2026-07-10T22:45:00Z",
            refresh_token_expires_at="2026-07-10T22:44:00Z",
        )


def test_me_is_structurally_token_and_secret_free():
    current = CurrentSession(
        workspace=_workspace(),
        principal=_principal(),
        available_commands=[],
    )
    payload = current.model_dump(mode="json")

    assert set(CurrentSession.model_fields) == {
        "workspace",
        "principal",
        "available_commands",
    }
    assert "access_token" not in json.dumps(payload)
    assert "refresh_token" not in json.dumps(payload)
    assert not ({"pin", "password_hash", "refresh_token_hash", "filesystem_path"} & set(payload))

    with pytest.raises(ValidationError):
        CurrentSession(
            workspace=_workspace(),
            principal=_principal(),
            available_commands=[],
            refresh_token="must-not-serialize",
        )


def test_session_and_device_summaries_are_bounded_token_free_and_utc():
    summary = SessionSummary(
        id="session_01J",
        device=DeviceSummary(name="Kevin's iPhone", platform="ios", app_version="1.0 (42)"),
        created_at="2026-07-10T10:00:00-04:00",
        last_seen_at="2026-07-10T15:00:00Z",
        expires_at="2026-08-09T14:00:00Z",
        is_current=True,
    )
    response = SessionListResponse(sessions=[summary])
    payload = jsonable_encoder(response)

    assert payload["sessions"][0]["created_at"] == "2026-07-10T14:00:00Z"
    assert payload["sessions"][0]["is_current"] is True
    assert "token" not in json.dumps(payload)
    assert set(DeviceSummary.model_fields) == {
        "name",
        "platform",
        "app_version",
    }
    assert DeviceSummary().model_dump() == {
        "name": None,
        "platform": None,
        "app_version": None,
    }
    with pytest.raises(ValidationError):
        DeviceSummary(
            name="Kevin's iPhone",
            platform="ios",
            app_version="1.0 (42)",
            installation_id="must-not-leak",
        )
    with pytest.raises(ValidationError):
        DeviceSummary(
            name="Kevin's iPhone",
            platform="ios",
            app_version="1.0 (42)",
            installation_id_hash="must-not-leak-either",
        )


def test_problem_details_match_rfc9457_extensions_and_swift_fields():
    problem = APIProblem(
        type="https://mise.example/problems/proofing-limit",
        title="Proofing limit reached",
        status=409,
        code="gallery.proofing_limit",
        detail="This section already has 20 selections.",
        request_id="req_01J",
        errors=[
            {
                "path": ["asset_id"],
                "message": "Selection would exceed the section target.",
                "code": "proofing_limit",
            }
        ],
    )
    assert problem.model_dump(mode="json") == {
        "type": "https://mise.example/problems/proofing-limit",
        "title": "Proofing limit reached",
        "status": 409,
        "code": "gallery.proofing_limit",
        "detail": "This section already has 20 selections.",
        "request_id": "req_01J",
        "errors": [
            {
                "path": ["asset_id"],
                "message": "Selection would exceed the section target.",
                "code": "proofing_limit",
            }
        ],
    }
    assert APIProblem(status=401).errors == []
    with pytest.raises(ValidationError):
        APIProblem(status=99)


def test_fastapi_validation_items_convert_to_swift_field_violations():
    problem = APIProblem.from_fastapi_validation(
        [
            {
                "loc": ["body", "device", "name"],
                "msg": "Field required",
                "type": "missing",
            },
            {
                "loc": ["body", "scopes", 2],
                "msg": "Input should be a valid string",
                "type": "string_type",
            },
        ],
        request_id="req_01J",
    )

    assert problem.status == 422
    assert problem.code == "request.validation_failed"
    assert problem.errors[0].path == ["body", "device", "name"]
    assert problem.errors[1].path == ["body", "scopes", "2"]
