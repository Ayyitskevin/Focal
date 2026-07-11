"""APNs HTTP/2 transport remains bounded, stable, and secret-safe."""

from __future__ import annotations

import httpx
import pytest

from app import apns

pytestmark = pytest.mark.unit

_TOKEN = "ab" * 32
_PAYLOAD = {"aps": {"alert": {"title": "Update", "body": "Open Mise."}}, "mise": {}}


def _provider() -> apns._ProviderConfig:
    return apns._ProviderConfig(
        team_id="A1B2C3D4E5",
        key_id="F6G7H8J9K0",
        topic="com.ayyitskevin.mise",
        environment="sandbox",
        private_key=b"unused-in-transport-tests",
    )


def test_payload_boundary_accepts_exact_shape_and_rejects_extra_or_oversized_data():
    assert apns._payload_bytes(_PAYLOAD).startswith(b'{"aps"')

    with pytest.raises(apns.APNsPayloadError):
        apns._payload_bytes({**_PAYLOAD, "token": _TOKEN})
    with pytest.raises(apns.APNsPayloadError):
        apns._payload_bytes(
            {
                "aps": _PAYLOAD["aps"],
                "mise": {"padding": "x" * 4_096},
            }
        )


def test_provider_token_rejection_refreshes_once_with_stable_request_identity(monkeypatch):
    calls = []

    def provider_token(value, *, rejected_token=None):
        return "new-provider-token" if rejected_token is not None else "old-provider-token"

    def send_once(value, **kwargs):
        calls.append((value, kwargs))
        if len(calls) == 1:
            return apns.APNsResponse(403, "InvalidProviderToken", kwargs["apns_id"])
        return apns.APNsResponse(200, None, kwargs["apns_id"])

    monkeypatch.setattr(apns, "provider_config", _provider)
    monkeypatch.setattr(apns, "_provider_token", provider_token)
    monkeypatch.setattr(apns, "_send_once", send_once)
    result = apns.send(
        device_token=_TOKEN,
        environment="sandbox",
        payload=_PAYLOAD,
        apns_id="018f632f-735d-7a16-8f31-2fb65d3f6e91",
        collapse_id="018f632f-735d-7a16-8f31-2fb65d3f6e92",
        expiration=1_800_000_000,
    )

    assert result.delivered
    assert [call[1]["provider_token"] for call in calls] == [
        "old-provider-token",
        "new-provider-token",
    ]
    for _, call in calls:
        assert call["apns_id"] == "018f632f-735d-7a16-8f31-2fb65d3f6e91"
        assert call["collapse_id"] == "018f632f-735d-7a16-8f31-2fb65d3f6e92"


def test_too_many_provider_updates_is_returned_without_an_immediate_refresh(monkeypatch):
    calls = []

    def send_once(value, **kwargs):
        calls.append((value, kwargs))
        return apns.APNsResponse(403, "TooManyProviderTokenUpdates", kwargs["apns_id"])

    monkeypatch.setattr(apns, "provider_config", _provider)
    monkeypatch.setattr(apns, "_provider_token", lambda *args, **kwargs: "provider-token")
    monkeypatch.setattr(apns, "_send_once", send_once)
    result = apns.send(
        device_token=_TOKEN,
        environment="sandbox",
        payload=_PAYLOAD,
        apns_id="018f632f-735d-7a16-8f31-2fb65d3f6e91",
        collapse_id="018f632f-735d-7a16-8f31-2fb65d3f6e92",
        expiration=1_800_000_000,
    )

    assert result.reason == "TooManyProviderTokenUpdates"
    assert len(calls) == 1


def test_rejected_provider_token_refresh_is_coalesced_and_rate_limited(monkeypatch):
    provider = _provider()
    identity = apns._jwt_identity(provider)
    encoded = []
    monkeypatch.setattr(apns, "_cached_jwt", ("old-token", 0.0, identity))
    monkeypatch.setattr(apns.time, "time", lambda: 2_000.0)

    def encode(*args, **kwargs):
        encoded.append((args, kwargs))
        return "new-token"

    monkeypatch.setattr(apns.jwt, "encode", encode)

    assert apns._provider_token(provider, rejected_token="old-token") == "new-token"
    assert apns._provider_token(provider, rejected_token="old-token") == "new-token"
    assert apns._provider_token(provider, rejected_token="new-token") == "new-token"
    assert len(encoded) == 1


def test_response_metadata_is_bounded_and_retry_after_is_parsed(monkeypatch):
    class Client:
        def post(self, url, **kwargs):
            assert url.endswith(_TOKEN)
            assert kwargs["headers"]["apns-push-type"] == "alert"
            return httpx.Response(
                429,
                headers={"apns-id": "response-id", "retry-after": "1800"},
                json={"reason": "TooManyRequests", "timestamp": 1_700_000_000},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(apns, "_provider_token", lambda *args, **kwargs: "provider-jwt")
    monkeypatch.setattr(apns, "_http_client", Client)
    result = apns._send_once(
        _provider(),
        device_token=_TOKEN,
        payload=_PAYLOAD,
        apns_id="request-id",
        collapse_id="collapse-id",
        expiration=1_800_000_000,
    )

    assert result == apns.APNsResponse(
        status_code=429,
        reason="TooManyRequests",
        apns_id="response-id",
        retry_after_seconds=1800,
        invalidated_at=1_700_000_000,
    )


def test_transport_error_never_reflects_token_bearing_request_url(monkeypatch):
    class Client:
        def post(self, url, **kwargs):
            request = httpx.Request("POST", url)
            raise httpx.ConnectError(f"could not send {_TOKEN}", request=request)

    monkeypatch.setattr(apns, "_provider_token", lambda *args, **kwargs: "provider-jwt")
    monkeypatch.setattr(apns, "_http_client", Client)

    with pytest.raises(apns.APNsTransportError) as caught:
        apns._send_once(
            _provider(),
            device_token=_TOKEN,
            payload=_PAYLOAD,
            apns_id="request-id",
            collapse_id="collapse-id",
            expiration=1_800_000_000,
        )
    assert _TOKEN not in str(caught.value)
    assert "device" not in str(caught.value).casefold()
